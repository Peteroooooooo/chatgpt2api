"""Mailboxes and domain routes from environment variables (.env).

    MAILBOXES=cf,gmail,qq                          names to load (optional)
    MAILBOX_<NAME>_KIND=cloudflare_temp_email | imap | gmail_api
    MAILBOX_<NAME>_DOMAINS=040601.xyz,040601.com   domains, full addresses or * routed here
    MAILBOX_<NAME>_<FIELD>=...                     provider fields, lower-cased:
        cloudflare_temp_email: BASE_URL, ADMIN_PASSWORD, SITE_PASSWORD
        imap:      PRESET (qq|gmail|163|outlook) or HOST, PORT, USERNAME, PASSWORD, FOLDERS (a,b)
        gmail_api: CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN
    MAILBOX_<NAME>_REQUIRE_RECIPIENT_MATCH=true|false

Without MAILBOXES every MAILBOX_<NAME>_* prefix is loaded. Names CF, GMAIL and QQ
get a default KIND/PRESET; APP_PASSWORD and AUTH_CODE are aliases of PASSWORD.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GLOBAL_KEYS = {"MAILBOXES", "MAILBOX_OTP_TIMEOUT_SECONDS", "MAILBOX_POLL_INTERVAL_SECONDS"}
NAME_DEFAULTS = {
    "CF": {"kind": "cloudflare_temp_email"},
    "GMAIL": {"kind": "imap", "preset": "gmail"},
    "QQ": {"kind": "imap", "preset": "qq"},
}
FIELD_ALIASES = {"app_password": "password", "auth_code": "password"}
LIST_FIELDS = {"folders"}
BOOL_FIELDS = {"require_recipient_match"}


def load_env_file(path: Path | None = None) -> None:
    """Read KEY=VALUE lines into os.environ; variables already set win."""
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _names(env: dict[str, str]) -> list[str]:
    listed = [item.strip().upper() for item in env.get("MAILBOXES", "").split(",") if item.strip()]
    if listed:
        return listed
    found: list[str] = []
    for key in env:
        if not key.startswith("MAILBOX_") or key in GLOBAL_KEYS:
            continue
        rest = key[len("MAILBOX_"):]
        name = rest.split("_", 1)[0]
        if name and "_" in rest and name not in found:
            found.append(name)
    return found


def mailbox_config_from_env(env: dict[str, str] | None = None) -> dict:
    """Return {"providers": {name: settings}, "routes": [...], ...} built from env."""
    if env is None:
        load_env_file()
        env = dict(os.environ)
    providers: dict[str, dict] = {}
    routes: list[dict] = []
    for name in _names(env):
        prefix = f"MAILBOX_{name}_"
        settings: dict = dict(NAME_DEFAULTS.get(name, {}))
        domains = ""
        for key, raw in env.items():
            if not key.startswith(prefix):
                continue
            field = key[len(prefix):].lower()
            value = str(raw).strip()
            if not value:
                continue
            field = FIELD_ALIASES.get(field, field)
            if field == "domains":
                domains = value
            elif field in LIST_FIELDS:
                settings[field] = [item.strip() for item in value.split(",") if item.strip()]
            elif field in BOOL_FIELDS:
                settings[field] = value.lower() in {"1", "true", "yes", "on"}
            else:
                settings[field] = value
        if name == "GMAIL" and settings.get("password"):
            settings["password"] = str(settings["password"]).replace(" ", "")
        if len(settings) <= len(NAME_DEFAULTS.get(name, {})) or not settings.get("kind"):
            continue
        key_name = name.lower()
        settings["source"] = "env"
        providers[key_name] = settings
        for match in domains.split(","):
            match = match.strip().lower()
            if match:
                routes.append({"match": match, "provider": key_name, "source": "env"})
    result: dict = {"providers": providers, "routes": routes}
    for key, field in (("MAILBOX_OTP_TIMEOUT_SECONDS", "otp_timeout_seconds"), ("MAILBOX_POLL_INTERVAL_SECONDS", "poll_interval_seconds")):
        try:
            if env.get(key):
                result[field] = int(env[key])
        except ValueError:
            pass
    return result


def providers_from_env() -> list:
    from services.mailbox.service import PROVIDER_CLASSES

    config = mailbox_config_from_env()
    return [
        PROVIDER_CLASSES[settings["kind"]](name, settings)
        for name, settings in config["providers"].items()
        if settings.get("kind") in PROVIDER_CLASSES
    ]