from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

from services.account_service import AccountService
from services.config import config
from services.openai_backend_api import InvalidAccessTokenError

SESSION_ENDED = (
    "oauth_refresh_http_401: {'message': 'Your session has ended. Please log in again.', "
    "'type': 'invalid_request_error', 'param': None, 'code': 'refresh_token_invalidated'}"
)


class MemoryStorage:
    def __init__(self, accounts: list[dict[str, Any]] | None = None) -> None:
        self.accounts = list(accounts or [])

    def load_accounts(self) -> list[dict[str, Any]]:
        return list(self.accounts)

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        self.accounts = list(accounts)

    def load_auth_keys(self) -> list[dict[str, Any]]:
        return []

    def save_auth_keys(self, auth_keys: list[dict[str, Any]]) -> None:
        pass

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def get_backend_info(self) -> dict[str, Any]:
        return {"type": "memory"}


def _account(**extra: Any) -> dict[str, Any]:
    return {
        "access_token": "old-token",
        "refresh_token": "rt",
        "status": "正常",
        "quota": 5,
        "created_at": "2026-01-01T00:00:00+00:00",
        **extra,
    }


class RevokedAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        patches = [
            patch("services.account_service.log_service", MagicMock()),
            patch.dict(config.data, {"auto_remove_invalid_accounts": False}),
            patch("services.openai_backend_api.OpenAIBackendAPI"),
        ]
        mocks = [item.start() for item in patches]
        for item in patches:
            self.addCleanup(item.stop)
        self.backend_class = mocks[2]
        self.backend_class.return_value.get_user_info.side_effect = InvalidAccessTokenError("token invalidated")

    def _check(self, service: AccountService, token: str = "old-token") -> None:
        with self.assertRaises(InvalidAccessTokenError):
            service.fetch_remote_info(token, "test")

    def test_session_ended_refresh_marks_abnormal_at_once(self) -> None:
        service = AccountService(MemoryStorage([_account()]))
        service._request_access_token_refresh = MagicMock(side_effect=RuntimeError(SESSION_ENDED))

        self._check(service)

        account = service.get_account("old-token")
        self.assertEqual(account["status"], "异常")
        self.assertEqual(account["invalid_count"], 1)
        self.assertIn("session has ended", account["last_token_refresh_error"])

    def test_session_ended_skips_new_account_grace(self) -> None:
        service = AccountService(MemoryStorage([_account(created_at=datetime.now(timezone.utc).isoformat())]))
        service._request_access_token_refresh = MagicMock(side_effect=RuntimeError(SESSION_ENDED))

        self._check(service)

        self.assertEqual(service.get_account("old-token")["status"], "异常")

    def test_transient_refresh_failures_keep_deferral(self) -> None:
        for error in ("oauth_refresh_http_500: upstream error", "Connection timed out", "oauth_refresh_http_401: unauthorized"):
            with self.subTest(error=error):
                service = AccountService(MemoryStorage([_account()]))
                service._request_access_token_refresh = MagicMock(side_effect=RuntimeError(error))

                self._check(service)

                account = service.get_account("old-token")
                self.assertEqual(account["status"], "正常")
                self.assertEqual(account["invalid_count"], 1)

    def test_refresh_success_but_still_rejected_keeps_deferral(self) -> None:
        service = AccountService(MemoryStorage([_account()]))
        service._request_access_token_refresh = MagicMock(return_value={"access_token": "new-token", "refresh_token": "rt2"})

        with self.assertRaises(InvalidAccessTokenError):
            service.fetch_remote_info("old-token", "test")

        account = service.get_account("new-token")
        self.assertEqual(account["status"], "正常")
        self.assertEqual(account["invalid_count"], 1)

    def test_stale_session_ended_error_does_not_count(self) -> None:
        service = AccountService(MemoryStorage([_account(
            refresh_token="",
            last_token_refresh_error=SESSION_ENDED,
            last_token_refresh_error_at="2026-01-01T00:00:00+00:00",
        )]))

        self._check(service)

        self.assertEqual(service.get_account("old-token")["status"], "正常")

    def test_session_ended_marker_needs_http_401(self) -> None:
        self.assertTrue(AccountService._is_session_ended_refresh_error(SESSION_ENDED))
        self.assertTrue(AccountService._is_session_ended_refresh_error("refresh_token: oauth_refresh_http_401: invalid_grant"))
        self.assertFalse(AccountService._is_session_ended_refresh_error("oauth_refresh_http_500: Your session has ended"))
        self.assertFalse(AccountService._is_session_ended_refresh_error("oauth_refresh_http_401: rate limited"))


if __name__ == "__main__":
    unittest.main()
