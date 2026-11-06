"""Model management rules: which upstream models are offered, and to which account types.

Rules live in data/model_rules.json:

    {
      "default_policy": "free_exposed",
      "models": {"gpt-6": {"exposed": true, "account_types": ["free", "Plus", ...]}},
      "features": {"search_model": "auto", ...}
    }

A model seen for the first time gets a rule from the default policy, which is then
stored, so later changes in which account types advertise it do not flip it.

ModelAccessTracker keeps the last 403 the conversation endpoint returned for an
(account type, model) pair, so the admin page can warn about models an account type
lists but cannot actually run. It never makes upstream calls of its own.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from services.config import DATA_DIR
from utils.log import logger

RULES_FILE = DATA_DIR / "model_rules.json"
ACCESS_FILE = DATA_DIR / "model_access.json"

# free_exposed: exposed when Free accounts (or anonymous) have it; Plus/Team/Pro-only models start hidden.
# all_exposed: every upstream model starts exposed, internal hidden-tagged ones included.
DEFAULT_POLICIES = ("free_exposed", "all_exposed", "all_hidden")
DEFAULT_POLICY = "free_exposed"
# Normalized account type names, as AccountService._normalize_account_type returns them.
KNOWN_ACCOUNT_TYPES = ("free", "Plus", "Team", "Pro", "ProLite", "Enterprise")
ANONYMOUS = "anonymous"
# Upstream tag for internal test models; they start hidden under every policy.
HIDDEN_TAG = "hidden"
FEATURE_DEFAULTS: dict[str, str] = {
    "search_model": "auto",
    "real_chat_model": "auto",
    "daily_chat_model": "auto",
}
MAX_MODEL_ID_LENGTH = 200


class ModelUnavailableError(RuntimeError):
    """A text model request that the rules or the pool cannot serve.

    status_code / code are what the API layer returns to the caller.
    """

    def __init__(self, message: str, *, status_code: int = 403, code: str = "model_not_available") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def iso_timestamp(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def clean_model_id(value: object) -> str:
    model_id = str(value or "").strip()
    if not model_id or len(model_id) > MAX_MODEL_ID_LENGTH:
        raise ValueError(f"模型名不合法：{str(value or '')[:40]!r}")
    return model_id


def clean_model_list(values: object) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError("模型列表必须是数组")
    return list(dict.fromkeys(clean_model_id(value) for value in values if str(value or "").strip()))


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - a broken file falls back to defaults
        logger.warning({"event": "model_rules_load_failed", "path": str(path), "error": str(exc)})
        return {}
    return data if isinstance(data, dict) else {}


class ModelRulesStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        default_policy: str = DEFAULT_POLICY,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._clock = clock
        self._lock = RLock()
        raw = _read_json(path)
        policy = str(raw.get("default_policy") or default_policy)
        self._default_policy = policy if policy in DEFAULT_POLICIES else DEFAULT_POLICY
        self._models: dict[str, dict[str, Any]] = {}
        for model_id, rule in (raw.get("models") or {}).items() if isinstance(raw.get("models"), dict) else ():
            normalized = self._normalize_rule(rule)
            if normalized is not None and str(model_id).strip():
                self._models[str(model_id).strip()] = normalized
        features = raw.get("features") if isinstance(raw.get("features"), dict) else {}
        self._features = {
            key: str(features.get(key) or "").strip() or default
            for key, default in FEATURE_DEFAULTS.items()
        }

    @staticmethod
    def _normalize_rule(raw: object) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        types = raw.get("account_types")
        if isinstance(types, list):
            account_types = list(dict.fromkeys(str(item).strip() for item in types if str(item or "").strip()))
        else:
            account_types = list(KNOWN_ACCOUNT_TYPES)
        return {
            "exposed": bool(raw.get("exposed", False)),
            "account_types": account_types,
            "source": "admin" if raw.get("source") == "admin" else "default",
            "added_at": str(raw.get("added_at") or ""),
        }

    def _save_locked(self) -> None:
        if self._path is None:
            return
        _write_json(self._path, {
            "default_policy": self._default_policy,
            "models": {model_id: self._models[model_id] for model_id in sorted(self._models)},
            "features": dict(self._features),
        })

    # ── rules ────────────────────────────────────────────────
    @property
    def default_policy(self) -> str:
        return self._default_policy

    def default_rule(
        self,
        model_id: str,
        available_types: Iterable[str],
        *,
        anonymous: bool = False,
        tags: Iterable[str] = (),
    ) -> dict[str, Any]:
        types = set(available_types)
        if self._default_policy == "all_exposed":
            exposed = True
        elif HIDDEN_TAG in set(tags):
            exposed = False
        elif self._default_policy == "all_hidden":
            exposed = False
        else:
            exposed = "free" in types or anonymous
        return {
            "exposed": exposed,
            "account_types": list(dict.fromkeys([*KNOWN_ACCOUNT_TYPES, *sorted(types)])),
            "source": "default",
            "added_at": iso_timestamp(self._clock()) or "",
        }

    def get_rule(self, model_id: str) -> dict[str, Any] | None:
        with self._lock:
            rule = self._models.get(model_id)
            return {**rule, "account_types": list(rule["account_types"])} if rule else None

    def all_rules(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {model_id: {**rule, "account_types": list(rule["account_types"])} for model_id, rule in self._models.items()}

    def add_defaults(self, rules: dict[str, dict[str, Any]]) -> list[str]:
        """Store default rules for models that have none yet. Returns the ids added."""

        with self._lock:
            added = [model_id for model_id in rules if model_id not in self._models]
            for model_id in added:
                normalized = self._normalize_rule(rules[model_id])
                if normalized is not None:
                    self._models[model_id] = normalized
            if added:
                self._save_locked()
        if added:
            logger.info({"event": "model_rules_defaults_added", "models": added})
        return added

    def update_rules(self, changes: dict[str, Any], defaults: dict[str, dict[str, Any]] | None = None) -> None:
        """Apply admin changes. defaults: the rule shown for models that have no stored rule yet.

        Every change is validated before anything is applied.
        """
        if not isinstance(changes, dict):
            raise ValueError("models 必须是对象")
        defaults = defaults or {}
        with self._lock:
            updated_rules: dict[str, dict[str, Any]] = {}
            for raw_id, change in changes.items():
                model_id = clean_model_id(raw_id)
                if not isinstance(change, dict):
                    raise ValueError(f"模型 {model_id} 的规则必须是对象")
                current = (
                    self._models.get(model_id)
                    or self._normalize_rule(defaults.get(model_id) or {})
                    or {}
                )
                updated = {**current, "account_types": list(current.get("account_types") or [])}
                if "exposed" in change:
                    if not isinstance(change["exposed"], bool):
                        raise ValueError(f"模型 {model_id} 的 exposed 必须是 true / false")
                    updated["exposed"] = change["exposed"]
                if "account_types" in change:
                    types = change["account_types"]
                    if not isinstance(types, list) or not all(isinstance(item, str) for item in types):
                        raise ValueError(f"模型 {model_id} 的 account_types 必须是字符串数组")
                    updated["account_types"] = list(dict.fromkeys(item.strip() for item in types if item.strip()))
                updated["source"] = "admin"
                updated["added_at"] = updated.get("added_at") or iso_timestamp(self._clock()) or ""
                updated_rules[model_id] = updated
            self._models.update(updated_rules)
            self._save_locked()

    def set_default_policy(self, policy: str) -> None:
        """Switch the policy. Rules the admin never touched are dropped, so the catalog
        recomputes them under the new policy; admin rules stay."""
        if policy not in DEFAULT_POLICIES:
            raise ValueError(f"default_policy 只能是 {', '.join(DEFAULT_POLICIES)}")
        with self._lock:
            changed = policy != self._default_policy
            self._default_policy = policy
            if changed:
                self._models = {
                    model_id: rule for model_id, rule in self._models.items() if rule.get("source") == "admin"
                }
            self._save_locked()

    # ── built-in feature models ──────────────────────────────
    def features(self) -> dict[str, str]:
        with self._lock:
            return dict(self._features)

    def feature_model(self, key: str) -> str:
        with self._lock:
            return self._features.get(key) or FEATURE_DEFAULTS[key]

    def update_features(self, changes: dict[str, Any]) -> None:
        if not isinstance(changes, dict):
            raise ValueError("features 必须是对象")
        prepared: dict[str, str] = {}
        for key, value in changes.items():
            if key not in FEATURE_DEFAULTS:
                raise ValueError(f"不认识的内置功能：{key}")
            prepared[key] = clean_model_id(value) if str(value or "").strip() else FEATURE_DEFAULTS[key]
        with self._lock:
            self._features.update(prepared)
            self._save_locked()


class ModelAccessTracker:
    """Last conversation 403 per (account type, model); cleared by a later success."""

    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._lock = RLock()
        raw = _read_json(path)
        items = raw.get("forbidden") if isinstance(raw.get("forbidden"), list) else []
        self._forbidden: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            if isinstance(item, dict) and item.get("account_type") and item.get("model"):
                key = (str(item["account_type"]), str(item["model"]))
                self._forbidden[key] = {
                    "account_type": key[0],
                    "model": key[1],
                    "status": int(item.get("status") or 403),
                    "at": str(item.get("at") or ""),
                    "detail": str(item.get("detail") or "")[:300],
                }

    def _save_locked(self) -> None:
        if self._path is None:
            return
        try:
            _write_json(self._path, {"forbidden": list(self._forbidden.values())})
        except Exception as exc:  # noqa: BLE001 - tracking must never break a request
            logger.warning({"event": "model_access_save_failed", "error": str(exc)})

    def record_forbidden(self, account_type: str, model: str, detail: str = "") -> None:
        key = (account_type or ANONYMOUS, str(model or "").strip())
        if not key[1]:
            return
        with self._lock:
            self._forbidden[key] = {
                "account_type": key[0],
                "model": key[1],
                "status": 403,
                "at": iso_timestamp(self._clock()) or "",
                "detail": str(detail or "")[:300],
            }
            self._save_locked()

    def record_ok(self, account_type: str, model: str) -> None:
        key = (account_type or ANONYMOUS, str(model or "").strip())
        with self._lock:
            if self._forbidden.pop(key, None) is not None:
                self._save_locked()

    def warnings(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._forbidden.values()]


model_rules_store = ModelRulesStore(RULES_FILE)
model_access_tracker = ModelAccessTracker(ACCESS_FILE)
