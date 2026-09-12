from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.accounts import _account_view, create_router
from services.account_service import AccountService
from services.openai_backend_api import OpenAIBackendAPI


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


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        return self.response


def make_backend(payload: dict[str, Any]) -> OpenAIBackendAPI:
    backend = object.__new__(OpenAIBackendAPI)
    backend.access_token = "test-token"
    backend.base_url = "https://chatgpt.com"
    backend.session = FakeSession(FakeResponse(payload))
    backend._headers = lambda *args, **kwargs: {}
    return backend


class PlusTrialEligibilityParsingTests(unittest.TestCase):
    def test_parses_eligible_plus_campaign(self) -> None:
        backend = make_backend(
            {
                "accounts": {
                    "default": {
                        "eligible_promo_campaigns": {
                            "plus": {"metadata": {"title": "One month free"}}
                        }
                    }
                }
            }
        )

        result = backend.get_plus_trial_eligibility()

        self.assertEqual(result, {"eligible": True, "title": "One month free"})

    def test_parses_empty_campaigns_as_ineligible(self) -> None:
        backend = make_backend(
            {"accounts": {"default": {"eligible_promo_campaigns": {}}}}
        )

        result = backend.get_plus_trial_eligibility()

        self.assertEqual(result, {"eligible": False, "title": ""})

    def test_missing_campaigns_is_detection_failure(self) -> None:
        backend = make_backend({"accounts": {"default": {}}})

        with self.assertRaisesRegex(RuntimeError, "missing eligible_promo_campaigns"):
            backend.get_plus_trial_eligibility()


class PlusTrialEligibilityPersistenceTests(unittest.TestCase):
    def test_successful_check_updates_only_promo_fields(self) -> None:
        storage = MemoryStorage(
            [
                {
                    "access_token": "test-token",
                    "sessionToken": "session-secret",
                    "has_plus_promo": False,
                    "promo_title": "",
                }
            ]
        )
        service = AccountService(storage)

        with patch("services.openai_backend_api.OpenAIBackendAPI") as backend_class:
            backend_class.return_value.get_plus_trial_eligibility.return_value = {
                "eligible": True,
                "title": "One month free",
            }
            result = service.check_plus_trial_eligibility("test-token")

        account = service.get_account("test-token")
        self.assertTrue(result["eligible"])
        self.assertEqual(account["session_token"], "session-secret")
        self.assertNotIn("sessionToken", account)
        self.assertTrue(account["has_plus_promo"])
        self.assertEqual(account["promo_title"], "One month free")

    def test_failed_check_preserves_last_confirmed_result(self) -> None:
        storage = MemoryStorage(
            [
                {
                    "access_token": "test-token",
                    "refresh_token": "refresh-secret",
                    "has_plus_promo": True,
                    "promo_title": "Confirmed title",
                }
            ]
        )
        service = AccountService(storage)

        with patch("services.openai_backend_api.OpenAIBackendAPI") as backend_class:
            backend_class.return_value.get_plus_trial_eligibility.side_effect = TimeoutError(
                "upstream timeout"
            )
            with self.assertRaises(TimeoutError):
                service.check_plus_trial_eligibility("test-token")

        account = service.get_account("test-token")
        self.assertTrue(account["has_plus_promo"])
        self.assertEqual(account["promo_title"], "Confirmed title")
        backend_class.return_value.close.assert_called_once()

    def test_chat_usability_test_persists_independent_status(self) -> None:
        storage = MemoryStorage(
            [
                {
                    "access_token": "test-token",
                    "has_plus_promo": True,
                    "promo_title": "Confirmed title",
                    "chat_test_status": "未测试",
                }
            ]
        )
        service = AccountService(storage)

        with (
            patch("services.openai_backend_api.OpenAIBackendAPI") as backend_class,
            patch("services.protocol.conversation.conversation_events", return_value=iter([
                {"type": "conversation.delta", "conversation_id": "conversation-1", "text": "OK"},
                {"type": "conversation.done", "conversation_id": "conversation-1", "text": "OK"},
            ])),
        ):
            result = service.test_chat_usability("test-token")

        account = service.get_account("test-token")
        self.assertEqual(result["status"], "可用")
        self.assertTrue(result["usable"])
        self.assertEqual(account["chat_test_status"], "可用")
        self.assertTrue(account["has_plus_promo"])
        self.assertEqual(account["promo_title"], "Confirmed title")
        backend_class.return_value.delete_conversation.assert_called_once_with("conversation-1")
        backend_class.return_value.close.assert_called_once()

    def test_chat_usability_failure_is_not_reported_as_usable(self) -> None:
        storage = MemoryStorage(
            [
                {
                    "access_token": "test-token",
                    "has_plus_promo": True,
                    "promo_title": "Confirmed title",
                    "chat_test_status": "可用",
                }
            ]
        )
        service = AccountService(storage)

        with (
            patch("services.openai_backend_api.OpenAIBackendAPI") as backend_class,
            patch(
                "services.protocol.conversation.conversation_events",
                side_effect=TimeoutError("upstream timeout"),
            ),
        ):
            result = service.test_chat_usability("test-token")

        account = service.get_account("test-token")
        self.assertEqual(result["status"], "测试失败")
        self.assertIsNone(result["usable"])
        self.assertEqual(account["chat_test_status"], "测试失败")
        self.assertTrue(account["has_plus_promo"])
        self.assertEqual(account["promo_title"], "Confirmed title")
        backend_class.return_value.close.assert_called_once()

    def test_account_view_exposes_indicator_without_renewal_secrets(self) -> None:
        view = _account_view(
            {
                "access_token": "test-token",
                "password": "password-secret",
                "refresh_token": "refresh-secret",
                "session_token": "session-secret",
            }
        )

        self.assertTrue(view["has_auto_renewal"])
        self.assertNotIn("password", view)
        self.assertNotIn("refresh_token", view)
        self.assertNotIn("session_token", view)

    def test_session_import_payload_adds_renewal_credential_to_existing_account(self) -> None:
        service = AccountService(MemoryStorage([{"access_token": "test-token"}]))

        result = service.add_account_items(
            [{"access_token": "test-token", "session_token": "session-secret"}]
        )

        account = service.get_account("test-token")
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(account["session_token"], "session-secret")
        self.assertTrue(_account_view(account)["has_auto_renewal"])


class AccountRouteAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(create_router())
        self.client = TestClient(app)

    def test_account_list_rejects_unauthenticated_requests(self) -> None:
        response = self.client.get("/api/accounts")

        self.assertEqual(response.status_code, 401)

    def test_eligibility_check_rejects_unauthenticated_requests(self) -> None:
        response = self.client.post(
            "/api/accounts/plus-trial-eligibility",
            json={"access_tokens": ["test-token"]},
        )

        self.assertEqual(response.status_code, 401)

    def test_chat_usability_check_rejects_unauthenticated_requests(self) -> None:
        response = self.client.post(
            "/api/accounts/chat-usability",
            json={"access_tokens": ["test-token"]},
        )

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
