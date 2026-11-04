from __future__ import annotations

import os
from pathlib import Path

from services.mailbox.base import MailboxProvider
from services.mailbox.cloudflare_temp_email import CloudflareTempEmailProvider
from services.mailbox.imap_mailbox import ImapProvider

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path | None = None) -> None:
    """Read KEY=VALUE lines into os.environ; variables already set in the environment win."""
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env(key: str) -> str:
    return os.environ.get(key, "").strip()


def providers_from_env() -> list[MailboxProvider]:
    """Build every mailbox provider whose required MAILBOX_* variables are filled in."""
    load_env_file()
    providers: list[MailboxProvider] = []

    if _env("MAILBOX_CF_BASE_URL") and _env("MAILBOX_CF_ADMIN_PASSWORD"):
        providers.append(
            CloudflareTempEmailProvider(
                "cloudflare_temp_email",
                {
                    "base_url": _env("MAILBOX_CF_BASE_URL"),
                    "admin_password": _env("MAILBOX_CF_ADMIN_PASSWORD"),
                    "site_password": _env("MAILBOX_CF_SITE_PASSWORD"),
                },
            )
        )

    if _env("MAILBOX_GMAIL_USERNAME") and _env("MAILBOX_GMAIL_APP_PASSWORD"):
        providers.append(
            ImapProvider(
                "gmail_imap",
                {
                    "preset": "gmail",
                    "username": _env("MAILBOX_GMAIL_USERNAME"),
                    "password": _env("MAILBOX_GMAIL_APP_PASSWORD").replace(" ", ""),
                },
            )
        )

    if _env("MAILBOX_QQ_USERNAME") and _env("MAILBOX_QQ_AUTH_CODE"):
        providers.append(
            ImapProvider(
                "qq_imap",
                {
                    "preset": "qq",
                    "username": _env("MAILBOX_QQ_USERNAME"),
                    "password": _env("MAILBOX_QQ_AUTH_CODE"),
                },
            )
        )

    return providers
