"""Log in to every mailbox configured in .env and print a short result for each.

Usage: uv run python scripts/check_mailboxes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.mailbox.base import MailboxError  # noqa: E402
from services.mailbox.env_config import providers_from_env  # noqa: E402


def main() -> int:
    providers = providers_from_env()
    if not providers:
        print("No mailbox configured: fill in the MAILBOX_* lines in .env first.")
        return 1
    failed = 0
    for provider in providers:
        try:
            print(f"[OK]   {provider.name}: {provider.test()}")
        except MailboxError as exc:
            failed += 1
            print(f"[FAIL] {provider.name}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
