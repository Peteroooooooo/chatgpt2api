from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from pathlib import Path
from threading import Lock

from services.mailbox.base import MailboxError, MailboxProvider, wait_for_code
from services.mailbox.cloudflare_temp_email import CloudflareTempEmailProvider
from services.mailbox.gmail_api import GmailApiProvider
from services.mailbox.imap_mailbox import ImapProvider

PROVIDER_CLASSES: dict[str, type[MailboxProvider]] = {
    CloudflareTempEmailProvider.kind: CloudflareTempEmailProvider,
    GmailApiProvider.kind: GmailApiProvider,
    ImapProvider.kind: ImapProvider,
}
SECRET_FIELDS = {"admin_password", "site_password", "client_secret", "refresh_token", "password"}
DEFAULTS = {"otp_timeout_seconds": 120, "poll_interval_seconds": 5}


def _domain_matches(domain: str, rule: str) -> bool:
    rule = rule.strip().lower().lstrip("@")
    return rule == "*" or domain == rule or domain.endswith("." + rule)


class MailboxService:
    """Maps an account email to the mailbox that receives its OpenAI login codes.

    data/mailboxes.json:
      providers: {name: {kind, ...settings}}
      routes:    [{match, provider}]  match = full address, domain, or "*"
      env_imported: true once the web page has saved; MAILBOX_* env is then ignored
    Until that first save, MAILBOX_* env mailboxes and routes are merged in.
    The most specific route wins: full address, then the longest domain.
    """

    def __init__(self, path: Path, env_config: dict | None = None):
        self.path = path
        self._lock = Lock()
        self._env = env_config if env_config is not None else self._read_env()
        self._file = self._load()
        self._data = self._merge()
        self._instances: dict[str, MailboxProvider] = {}

    @staticmethod
    def _read_env() -> dict:
        try:
            from services.mailbox.env_config import mailbox_config_from_env

            return mailbox_config_from_env()
        except Exception as exc:
            print(f"[mailbox] cannot read MAILBOX_* env: {exc}")
            return {"providers": {}, "routes": []}

    def _merge(self) -> dict:
        if self._file.get("env_imported"):
            merged = {"providers": copy.deepcopy(self._file["providers"]), "routes": copy.deepcopy(self._file["routes"])}
            for key, default in DEFAULTS.items():
                merged[key] = self._file.get(key, default)
            return merged
        env_providers = self._env.get("providers") or {}
        merged = {
            "providers": {**self._file["providers"], **copy.deepcopy(env_providers)},
            "routes": [*copy.deepcopy(self._env.get("routes") or []), *copy.deepcopy(self._file["routes"])],
        }
        for key, default in DEFAULTS.items():
            merged[key] = self._env.get(key, self._file.get(key, default))
        merged["env_pending"] = bool(env_providers)
        return merged

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except Exception as exc:
            print(f"[mailbox] cannot read {self.path}: {exc}")
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("providers", {})
        data.setdefault("routes", [])
        for key, value in DEFAULTS.items():
            data.setdefault(key, value)
        return data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._file, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    def public_settings(self) -> dict:
        with self._lock:
            data = copy.deepcopy(self._data)
        for settings in data["providers"].values():
            for key in SECRET_FIELDS:
                if key in settings:
                    settings[f"has_{key}"] = bool(str(settings[key] or "").strip())
                    settings[key] = ""
        return data

    def update_settings(self, incoming: dict) -> dict:
        """Replace all mailbox settings with what the web page sent.

        The first save also takes over the MAILBOX_* env mailboxes: from then on the
        file is the only source and env values are ignored. An empty secret field
        keeps the previous value (from the file, or from env before the takeover).
        """
        with self._lock:
            previous_all = {**(self._env.get("providers") or {}), **self._file["providers"]} if not self._file.get("env_imported") else self._file["providers"]
            providers_in = incoming.get("providers") if isinstance(incoming.get("providers"), dict) else {}
            providers: dict[str, dict] = {}
            for name, settings in providers_in.items():
                if not isinstance(settings, dict):
                    continue
                name = str(name).strip()
                kind = str(settings.get("kind") or "").strip()
                if not name or kind not in PROVIDER_CLASSES:
                    raise ValueError(f"mailbox '{name}': unknown kind '{kind}'")
                previous = previous_all.get(name, {})
                clean = {k: v for k, v in settings.items() if not str(k).startswith("has_") and k != "source"}
                for key in SECRET_FIELDS:
                    if not str(clean.get(key) or "").strip() and previous.get(key):
                        clean[key] = previous[key]
                providers[name] = clean
            known = set(providers)
            routes: list[dict] = []
            seen: set[tuple[str, str]] = set()
            for route in incoming.get("routes") or []:
                if not isinstance(route, dict):
                    continue
                match = str(route.get("match") or route.get("domain") or "").strip().lower()
                provider = str(route.get("provider") or "").strip()
                if not match or not provider or (match, provider) in seen:
                    continue
                if provider not in known:
                    raise ValueError(f"route '{match}' points to unknown mailbox '{provider}'")
                seen.add((match, provider))
                routes.append({"match": match, "provider": provider})
            next_file = {"providers": providers, "routes": routes, "env_imported": True}
            for key, default in DEFAULTS.items():
                try:
                    floor = 10 if key == "otp_timeout_seconds" else 1
                    next_file[key] = max(floor, int(incoming.get(key, self._file.get(key, default))))
                except (TypeError, ValueError):
                    next_file[key] = default
            self._file = next_file
            self._data = self._merge()
            self._instances.clear()
            self._save()
        return self.public_settings()

    def _instance(self, name: str) -> MailboxProvider:
        settings = self._data["providers"].get(name)
        if not isinstance(settings, dict):
            raise MailboxError(f"mailbox '{name}' is not configured")
        instance = self._instances.get(name)
        if instance is None:
            cls = PROVIDER_CLASSES.get(str(settings.get("kind") or ""))
            if cls is None:
                raise MailboxError(f"mailbox '{name}': unknown kind")
            instance = cls(name, settings)
            self._instances[name] = instance
        return instance

    def resolve(self, address: str) -> MailboxProvider | None:
        address = str(address or "").strip().lower()
        if "@" not in address:
            return None
        domain = address.rsplit("@", 1)[1]
        with self._lock:
            best: tuple[int, str] | None = None
            for route in self._data["routes"]:
                match = route["match"]
                if "@" in match and not match.startswith("@"):
                    score = 10_000 if match == address else -1
                elif _domain_matches(domain, match):
                    score = 0 if match == "*" else len(match)
                else:
                    score = -1
                if score >= 0 and (best is None or score > best[0]):
                    best = (score, route["provider"])
            return self._instance(best[1]) if best else None

    def wait_for_code(self, address: str, since: datetime, exclude: set[str] | None = None) -> str:
        provider = self.resolve(address)
        if provider is None:
            raise MailboxError(f"no mailbox route for {address}")
        return wait_for_code(
            provider,
            address,
            since,
            timeout=float(self._data.get("otp_timeout_seconds") or 120),
            interval=float(self._data.get("poll_interval_seconds") or 5),
            require_recipient_match=bool(provider.settings.get("require_recipient_match", True)),
            exclude=exclude,
        )

    def test(self, name: str) -> dict:
        with self._lock:
            provider = self._instance(name)
        try:
            return provider.test()
        except MailboxError as exc:
            return {"ok": False, "error": str(exc)}


def _default_path() -> Path:
    from services.config import DATA_DIR

    return DATA_DIR / "mailboxes.json"


mailbox_service = MailboxService(_default_path())
