from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import RLock
from typing import Any

from services.account_service import AccountService, account_service
from services.model_rules import (
    ANONYMOUS,
    ModelAccessTracker,
    ModelRulesStore,
    ModelUnavailableError,
    iso_timestamp as _iso,
    model_access_tracker,
    model_rules_store,
)
from services.openai_backend_api import OpenAIBackendAPI
from utils.log import logger

__all__ = [
    "ModelCatalogService",
    "ModelRoute",
    "ModelUnavailableError",
    "account_type_label",
    "model_catalog_service",
]

_TYPE_LABELS = {"free": "Free"}


def account_type_label(account_type: str) -> str:
    return _TYPE_LABELS.get(account_type, account_type)


@dataclass(frozen=True)
class ModelRoute:
    account_types: frozenset[str]
    allow_anonymous: bool = False
    # exposed: the model's rule lets it out through the public API.
    exposed: bool = True
    # known: at least one account type (or anonymous) advertises the model.
    known: bool = True
    # any_account: no type filter at all (catalog empty, or a built-in model the catalog does not list).
    any_account: bool = False


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "_meta"}


class ModelCatalogService:
    """Caches the model catalogs advertised to each active account type and applies the model rules."""

    def __init__(
        self,
        accounts: AccountService,
        *,
        backend_factory: Callable[..., Any] = OpenAIBackendAPI,
        cache_ttl_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        rules: ModelRulesStore | None = None,
        access_tracker: ModelAccessTracker | None = None,
    ) -> None:
        self._accounts = accounts
        self._backend_factory = backend_factory
        self._cache_ttl_seconds = max(1.0, float(cache_ttl_seconds))
        self._clock = clock
        self._wall_clock = wall_clock
        self._rules = rules if rules is not None else ModelRulesStore(None)
        self._access = access_tracker if access_tracker is not None else ModelAccessTracker(None)
        self._lock = RLock()
        self._expires_at = 0.0
        self._account_signature: tuple[tuple[str, int], ...] = ()
        self._anonymous_models: dict[str, dict[str, Any]] = {}
        self._models_by_account_type: dict[str, dict[str, dict[str, Any]]] = {}
        self._fetched_at: dict[str, float] = {}
        self._failed: set[str] = set()
        self._refreshed_at = 0.0

    @property
    def rules(self) -> ModelRulesStore:
        return self._rules

    @property
    def access_tracker(self) -> ModelAccessTracker:
        return self._access

    @staticmethod
    def _model_map(result: object) -> dict[str, dict[str, Any]]:
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise TypeError("upstream model response has no data list")
        models: dict[str, dict[str, Any]] = {}
        for item in result["data"]:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if model_id and model_id not in models:
                models[model_id] = dict(item)
        return models

    def _active_accounts_by_type(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for account in self._accounts.list_accounts():
            if not isinstance(account, dict) or account.get("status") in {"禁用", "异常"}:
                continue
            access_token = str(account.get("access_token") or "").strip()
            account_type = self._accounts._normalize_account_type(account.get("type"))
            if access_token and account_type:
                groups.setdefault(account_type, []).append(access_token)
        return groups

    @staticmethod
    def _signature(groups: dict[str, list[str]]) -> tuple[tuple[str, int], ...]:
        return tuple(
            (account_type, len(tokens))
            for account_type, tokens in sorted(groups.items())
        )

    def _fetch_models(self, access_token: str = "") -> dict[str, dict[str, Any]]:
        backend = self._backend_factory(access_token=access_token)
        try:
            return self._model_map(backend.list_models())
        finally:
            backend.close()

    def _fetch_account_type_models(
        self,
        account_type: str,
        access_tokens: list[str],
    ) -> dict[str, dict[str, Any]] | None:
        attempted_tokens: set[str] = set()
        last_error: Exception | None = None
        for access_token in access_tokens:
            try:
                resolved_token = self._accounts.refresh_access_token(
                    access_token,
                    event="list_models",
                ) or access_token
                if resolved_token in attempted_tokens:
                    continue
                attempted_tokens.add(resolved_token)
                return self._fetch_models(resolved_token)
            except Exception as exc:  # noqa: BLE001 - try the next account for any upstream failure
                last_error = exc
        if last_error is not None:
            logger.warning({
                "event": "model_catalog_account_type_failed",
                "account_type": account_type,
                "error_type": type(last_error).__name__,
            })
        return None

    def _refresh(self, groups: dict[str, list[str]], signature: tuple[tuple[str, int], ...]) -> None:
        models_by_account_type: dict[str, dict[str, dict[str, Any]]] = {}
        failed: set[str] = set()
        now = self._wall_clock()
        with ThreadPoolExecutor(max_workers=min(4, len(groups) + 1)) as executor:
            anonymous_future = executor.submit(self._fetch_models)
            account_futures = {
                account_type: executor.submit(
                    self._fetch_account_type_models,
                    account_type,
                    access_tokens,
                )
                for account_type, access_tokens in groups.items()
            }
            try:
                anonymous_models = anonymous_future.result()
                self._fetched_at[ANONYMOUS] = now
            except Exception as exc:  # noqa: BLE001 - retain cached models on upstream failure
                logger.warning({
                    "event": "model_catalog_anonymous_failed",
                    "error_type": type(exc).__name__,
                })
                anonymous_models = self._anonymous_models
                failed.add(ANONYMOUS)

            for account_type, future in account_futures.items():
                models = future.result()
                if models is not None:
                    models_by_account_type[account_type] = models
                    self._fetched_at[account_type] = now
                else:
                    failed.add(account_type)
                    if account_type in self._models_by_account_type:
                        models_by_account_type[account_type] = self._models_by_account_type[account_type]

        self._anonymous_models = anonymous_models
        self._models_by_account_type = models_by_account_type
        for account_type in list(self._fetched_at):
            if account_type != ANONYMOUS and account_type not in groups:
                self._fetched_at.pop(account_type, None)
        self._failed = failed
        self._account_signature = signature
        self._refreshed_at = now
        self._expires_at = self._clock() + self._cache_ttl_seconds
        self._store_default_rules_locked(groups)

    def _store_default_rules_locked(self, groups: dict[str, list[str]]) -> None:
        """Persist default rules for first-seen models, but only once every active type answered.

        If a type has never been fetched, a model it has would look like it belongs only to the
        other types, so its default would be wrong; such models keep a computed default until a
        complete fetch.
        """

        if any(account_type not in self._models_by_account_type for account_type in groups):
            return
        new_rules = {
            model_id: self._default_rule_locked(model_id)
            for model_id in self._all_model_ids_locked()
            if self._rules.get_rule(model_id) is None
        }
        if new_rules:
            try:
                self._rules.add_defaults(new_rules)
            except Exception as exc:  # noqa: BLE001 - the computed default still applies in memory
                logger.warning({"event": "model_rules_save_failed", "error": str(exc)})

    def _ensure_catalog(self, *, force: bool = False) -> None:
        groups = self._active_accounts_by_type()
        signature = self._signature(groups)
        with self._lock:
            if not force and signature == self._account_signature and self._clock() < self._expires_at:
                return
            self._refresh(groups, signature)

    # ── rule application ─────────────────────────────────────
    def _all_model_ids_locked(self) -> list[str]:
        ids = set(self._anonymous_models)
        for models in self._models_by_account_type.values():
            ids.update(models)
        return sorted(ids)

    def _item_locked(self, model_id: str) -> dict[str, Any] | None:
        for account_type in sorted(self._models_by_account_type):
            item = self._models_by_account_type[account_type].get(model_id)
            if item is not None:
                return item
        return self._anonymous_models.get(model_id)

    def _meta_locked(self, model_id: str) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        sources = [models.get(model_id) for _type, models in sorted(self._models_by_account_type.items())]
        sources.append(self._anonymous_models.get(model_id))
        tags: set[str] = set()
        for item in sources:
            if not isinstance(item, dict) or not isinstance(item.get("_meta"), dict):
                continue
            item_meta = item["_meta"]
            meta.setdefault("title", str(item_meta.get("title") or ""))
            meta.setdefault("reasoning_type", str(item_meta.get("reasoning_type") or ""))
            tags.update(str(tag) for tag in item_meta.get("tags") or [] if tag)
        meta["tags"] = sorted(tags)
        return meta

    def _available_types_locked(self, model_id: str) -> frozenset[str]:
        return frozenset(
            account_type
            for account_type, models in self._models_by_account_type.items()
            if model_id in models
        )

    def _default_rule_locked(self, model_id: str) -> dict[str, Any]:
        return self._rules.default_rule(
            model_id,
            self._available_types_locked(model_id),
            anonymous=model_id in self._anonymous_models,
            tags=self._meta_locked(model_id).get("tags") or (),
        )

    def _rule_locked(self, model_id: str) -> dict[str, Any]:
        return self._rules.get_rule(model_id) or self._default_rule_locked(model_id)

    def _route_locked(self, model_id: str) -> ModelRoute:
        available = self._available_types_locked(model_id)
        anonymous = model_id in self._anonymous_models
        if not available and not anonymous:
            if model_id == "auto" and not self._anonymous_models and not self._models_by_account_type:
                # Nothing could be fetched at all: keep the old unfiltered rotation for the default model.
                return ModelRoute(account_types=frozenset(), any_account=True)
            return ModelRoute(account_types=frozenset(), allow_anonymous=False, exposed=False, known=False)
        rule = self._rule_locked(model_id)
        return ModelRoute(
            account_types=available & frozenset(rule["account_types"]),
            allow_anonymous=anonymous,
            exposed=bool(rule["exposed"]),
            known=True,
        )

    def list_models(self) -> dict[str, Any]:
        """Models offered through /v1/models: exposed, and runnable by an allowed type or anonymously."""

        self._ensure_catalog()
        with self._lock:
            data = []
            for model_id in self._all_model_ids_locked():
                route = self._route_locked(model_id)
                if route.exposed and (route.account_types or route.allow_anonymous):
                    data.append(_public_item(self._item_locked(model_id) or {"id": model_id}))
        return {"object": "list", "data": data}

    def route_for_model(self, model: str) -> ModelRoute:
        model = str(model or "").strip()
        self._ensure_catalog()
        with self._lock:
            return self._route_locked(model)

    def resolve_route(self, model: str, *, internal: bool = False) -> ModelRoute:
        """Route for a text request, or ModelUnavailableError with a reason for the caller.

        internal=True is for built-in features (search, tests): the exposed switch does not
        apply, and a model the catalog does not list at all runs on any account.
        """

        model = str(model or "").strip() or "auto"
        route = self.route_for_model(model)
        if route.any_account:
            return route
        if not route.known:
            if internal:
                return ModelRoute(account_types=frozenset(), any_account=True)
            raise ModelUnavailableError(
                f"没有模型 {model}：号池里没有哪类账号提供这个模型",
                status_code=400,
                code="model_not_found",
            )
        if not internal and not route.exposed:
            raise ModelUnavailableError(
                f"模型 {model} 没有开放，请换一个模型",
                status_code=403,
                code="model_not_exposed",
            )
        return route

    # ── 403 tracking ─────────────────────────────────────────
    def account_type_for_token(self, access_token: str) -> str:
        if not access_token:
            return ANONYMOUS
        account = self._accounts.get_account(access_token) or {}
        return self._accounts._normalize_account_type(account.get("type")) or "unknown"

    def record_conversation_result(self, access_token: str, model: str, exc: BaseException | None = None) -> None:
        """Remember a conversation 403 for (account type, model); a later success clears it."""

        try:
            model = str(model or "").strip()
            if not model:
                return
            if exc is None:
                if self._access.warnings():
                    self._access.record_ok(self.account_type_for_token(access_token), model)
                return
            if getattr(exc, "status_code", None) == 403 and "conversation" in str(getattr(exc, "context", "")):
                self._access.record_forbidden(
                    self.account_type_for_token(access_token),
                    model,
                    str(exc)[:300],
                )
        except Exception as error:  # noqa: BLE001 - tracking must never break a request
            logger.warning({"event": "model_access_record_failed", "error": str(error)})

    # ── admin view ───────────────────────────────────────────
    def catalog_view(self, *, force: bool = False) -> dict[str, Any]:
        self._ensure_catalog(force=force)
        groups = self._active_accounts_by_type()
        warnings = self._access.warnings()
        with self._lock:
            types = sorted(set(groups) | set(self._models_by_account_type))
            account_types = [
                {
                    "type": account_type,
                    "label": account_type_label(account_type),
                    "accounts": len(groups.get(account_type, [])),
                    "models": sorted(self._models_by_account_type.get(account_type, {})),
                    "fetched_at": _iso(self._fetched_at.get(account_type)),
                    "failed": account_type in self._failed,
                }
                for account_type in types
            ]
            anonymous = {
                "models": sorted(self._anonymous_models),
                "fetched_at": _iso(self._fetched_at.get(ANONYMOUS)),
                "failed": ANONYMOUS in self._failed,
            }
            models = []
            summary = {"exposed": 0, "hidden": 0, "no_account": 0}
            for model_id in self._all_model_ids_locked():
                route = self._route_locked(model_id)
                rule = self._rule_locked(model_id)
                stored = self._rules.get_rule(model_id) is not None
                meta = self._meta_locked(model_id)
                if not route.exposed:
                    result = "hidden"
                elif route.account_types or route.allow_anonymous:
                    result = "exposed"
                else:
                    result = "no_account"
                summary[result] += 1
                models.append({
                    "id": model_id,
                    "title": meta.get("title") or "",
                    "reasoning_type": meta.get("reasoning_type") or "",
                    "tags": meta.get("tags") or [],
                    "available_types": sorted(self._available_types_locked(model_id)),
                    "anonymous": model_id in self._anonymous_models,
                    "exposed": bool(rule["exposed"]),
                    "account_types": list(rule["account_types"]),
                    "rule_source": rule.get("source") if stored else "pending",
                    "served_by": sorted(route.account_types),
                    "result": result,
                    "warnings": [item for item in warnings if item["model"] == model_id],
                })
            refreshed_at = _iso(self._refreshed_at)
        return {
            "refreshed_at": refreshed_at,
            "cache_ttl_seconds": int(self._cache_ttl_seconds),
            "default_policy": self._rules.default_policy,
            "account_types": account_types,
            "anonymous": anonymous,
            "models": models,
            "summary": summary,
            "warnings": warnings,
        }

    def normalize_account_type(self, value: object) -> str | None:
        return self._accounts._normalize_account_type(value)


model_catalog_service = ModelCatalogService(
    account_service,
    rules=model_rules_store,
    access_tracker=model_access_tracker,
)
