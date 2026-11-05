from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import ai as ai_api
from api import models as models_api
from api.errors import install_exception_handlers
from api.support import key_allowed_models, require_model_allowed
from services.account_service import AccountService
from services.auth_service import AuthService
from services.log_service import LoggedCall
from services.model_rules import ModelAccessTracker, ModelRulesStore, ModelUnavailableError
from services.model_service import ModelCatalogService
from services.protocol import openai_search
from services.storage.json_storage import JSONStorageBackend
from utils.helper import UpstreamHTTPError

# Model lists measured on the live pool on 2026-11-06.
GATE_12 = "chatgpt_alpha_model_external_access_reserved_gate_12"
GATE_13 = "chatgpt_alpha_model_external_access_reserved_gate_13"
FREE_MODELS = [
    "auto", "gpt-5-6", "gpt-5-6-mini", "gpt-5-6-t-mini", "gpt-5-6-t-mini-mini",
    "gpt-5.6-terra-wm", "gpt-5.6-luna-wm", "gpt-6", "gpt-6-mini", "gpt-6-t-mini",
    "gpt-6-t-mini-mini", "research", GATE_12, GATE_13,
]
PLUS_ONLY = [
    "gpt-5-6-instant", "gpt-5-6-thinking", "gpt-5.6-sol-wm", "gpt-6-instant", "gpt-6-thinking",
    "gpt-6-astra-wm", "gpt-6-sol-wm", "gpt-6-luna-wm", "gpt-6.1-sol-wm", "gpt-6.1-terra-wm",
]
PLUS_MODELS = [
    model for model in FREE_MODELS if model not in {"auto", "gpt-5-6-t-mini-mini", "gpt-6-t-mini-mini"}
] + PLUS_ONLY
ANON_MODELS = ["auto", "gpt-5-6", "gpt-5-6-mini", "gpt-6", "gpt-6-mini"]


def model_list(model_ids: list[str]) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "chatgpt",
                "permission": [],
                "root": model_id,
                "parent": None,
                "_meta": {
                    "title": model_id.upper(),
                    "reasoning_type": "none",
                    "tags": ["history_off_approved", *(["hidden"] if "reserved_gate" in model_id else [])],
                },
            }
            for model_id in model_ids
        ],
    }


class FakeBackend:
    def __init__(self, access_token: str, outcomes: dict[str, object]) -> None:
        self.access_token = access_token
        self._outcomes = outcomes

    def list_models(self) -> dict:
        outcome = self._outcomes[self.access_token]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self) -> None:
        pass


class PoolFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.accounts = AccountService(JSONStorageBackend(self.root / "accounts.json"))
        self.accounts.add_account_items([
            {"access_token": "free-1", "type": "free", "status": "正常"},
            {"access_token": "free-2", "type": "free", "status": "正常"},
            {"access_token": "plus-1", "type": "plus", "status": "正常"},
        ])
        self.accounts.refresh_access_token = lambda token, **_kwargs: token
        self.outcomes: dict[str, object] = {
            "": model_list(ANON_MODELS),
            "free-1": model_list(FREE_MODELS),
            "free-2": model_list(FREE_MODELS),
            "plus-1": model_list(PLUS_MODELS),
        }
        self.rules_path = self.root / "model_rules.json"
        self.now = 1000.0
        self.catalog = self.make_catalog()

    def make_catalog(self) -> ModelCatalogService:
        return ModelCatalogService(
            self.accounts,
            backend_factory=lambda access_token="": FakeBackend(access_token, self.outcomes),
            clock=lambda: self.now,
            wall_clock=lambda: 1_790_000_000.0,
            rules=ModelRulesStore(self.rules_path),
            access_tracker=ModelAccessTracker(self.root / "model_access.json"),
        )

    def listed(self) -> set[str]:
        return {item["id"] for item in self.catalog.list_models()["data"]}


class DefaultRuleTests(PoolFixture):
    def test_free_models_are_exposed_and_plus_only_and_hidden_tagged_are_not(self) -> None:
        listed = self.listed()

        self.assertEqual(listed, set(FREE_MODELS) - {GATE_12, GATE_13})
        for model in PLUS_ONLY:
            self.assertNotIn(model, listed)
        self.assertNotIn("_meta", self.catalog.list_models()["data"][0])

    def test_first_seen_defaults_are_stored_and_do_not_flip_later(self) -> None:
        self.catalog.list_models()
        stored = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.assertFalse(stored["models"]["gpt-6-astra-wm"]["exposed"])
        self.assertTrue(stored["models"]["gpt-6"]["exposed"])
        self.assertEqual(stored["default_policy"], "free_exposed")

        # Free accounts later advertise astra too: the stored rule keeps it hidden.
        self.outcomes["free-1"] = self.outcomes["free-2"] = model_list([*FREE_MODELS, "gpt-6-astra-wm"])
        self.now += 301
        self.assertNotIn("gpt-6-astra-wm", self.listed())

    def test_defaults_wait_until_every_active_type_answered(self) -> None:
        self.outcomes["free-1"] = self.outcomes["free-2"] = RuntimeError("upstream down")
        self.catalog.list_models()

        self.assertFalse(self.rules_path.exists())

        self.outcomes["free-1"] = self.outcomes["free-2"] = model_list(FREE_MODELS)
        self.now += 301
        self.catalog.list_models()
        stored = json.loads(self.rules_path.read_text(encoding="utf-8"))
        # gpt-6 was Plus-only during the failed round, but its stored default saw Free too.
        self.assertTrue(stored["models"]["gpt-6"]["exposed"])

    def test_auto_goes_only_to_types_that_list_it(self) -> None:
        route = self.catalog.resolve_route("auto")

        self.assertEqual(route.account_types, frozenset({"free"}))
        self.assertTrue(route.allow_anonymous)

    def test_empty_catalog_keeps_unfiltered_auto(self) -> None:
        for key in self.outcomes:
            self.outcomes[key] = RuntimeError("upstream down")

        route = self.catalog.resolve_route("auto")

        self.assertTrue(route.any_account)


class RuleApplicationTests(PoolFixture):
    def test_exposed_model_without_allowed_type_disappears_and_reports_no_account(self) -> None:
        self.catalog.list_models()
        all_but_plus = [t for t in self.catalog.rules.get_rule("gpt-6-astra-wm")["account_types"] if t != "Plus"]
        self.catalog.rules.update_rules({"gpt-6-astra-wm": {"exposed": True, "account_types": all_but_plus}})

        self.assertNotIn("gpt-6-astra-wm", self.listed())
        view = {item["id"]: item for item in self.catalog.catalog_view()["models"]}
        self.assertEqual(view["gpt-6-astra-wm"]["result"], "no_account")
        self.assertEqual(view["gpt-6-astra-wm"]["available_types"], ["Plus"])

    def test_enabling_a_plus_model_routes_only_to_plus(self) -> None:
        self.catalog.list_models()
        self.catalog.rules.update_rules({"gpt-6-thinking": {"exposed": True}})

        self.assertIn("gpt-6-thinking", self.listed())
        route = self.catalog.resolve_route("gpt-6-thinking")
        self.assertEqual(route.account_types, frozenset({"Plus"}))

    def test_type_unchecked_for_shared_model_keeps_other_types(self) -> None:
        self.catalog.list_models()
        types = [t for t in self.catalog.rules.get_rule("gpt-6")["account_types"] if t != "free"]
        self.catalog.rules.update_rules({"gpt-6": {"account_types": types}})

        route = self.catalog.resolve_route("gpt-6")
        self.assertEqual(route.account_types, frozenset({"Plus"}))
        self.assertIn("gpt-6", self.listed())

    def test_hidden_model_request_raises_403_and_unknown_raises_400(self) -> None:
        with self.assertRaises(ModelUnavailableError) as hidden:
            self.catalog.resolve_route(GATE_12)
        with self.assertRaises(ModelUnavailableError) as unknown:
            self.catalog.resolve_route("gpt-9")

        self.assertEqual((hidden.exception.status_code, hidden.exception.code), (403, "model_not_exposed"))
        self.assertEqual((unknown.exception.status_code, unknown.exception.code), (400, "model_not_found"))

    def test_partial_invalid_update_changes_nothing(self) -> None:
        self.catalog.list_models()
        before = self.catalog.rules.get_rule("gpt-6")

        with self.assertRaises(ValueError):
            self.catalog.rules.update_rules({"gpt-6": {"exposed": False}, "gpt-6-mini": {"exposed": "yes"}})

        self.assertEqual(self.catalog.rules.get_rule("gpt-6"), before)

    def test_text_account_selection_follows_rules(self) -> None:
        with mock.patch("services.model_service.model_catalog_service", self.catalog):
            self.catalog.rules.update_rules({"gpt-6-thinking": {"exposed": True}})
            self.assertEqual(self.accounts.get_text_access_token(model="gpt-6-thinking"), "plus-1")
            self.assertEqual(
                {self.accounts.get_text_access_token(model="auto") for _ in range(4)},
                {"free-1", "free-2"},
            )
            with self.assertRaisesRegex(ModelUnavailableError, "没有开放"):
                self.accounts.get_text_access_token(model="gpt-6-astra-wm")


class AccessWarningTests(PoolFixture):
    def test_conversation_403_is_shown_until_a_success(self) -> None:
        error = UpstreamHTTPError("/backend-api/f/conversation", 403, {"detail": "forbidden"})
        self.catalog.record_conversation_result("free-1", "gpt-5.6-terra-wm", error)
        self.catalog.record_conversation_result("free-1", "gpt-6", UpstreamHTTPError("/backend-api/f/conversation", 429, {}))

        view = {item["id"]: item for item in self.catalog.catalog_view()["models"]}
        warnings = view["gpt-5.6-terra-wm"]["warnings"]
        self.assertEqual([(item["account_type"], item["status"]) for item in warnings], [("free", 403)])
        self.assertEqual(view["gpt-6"]["warnings"], [])
        # Persisted in a small json, so a restart keeps the warning.
        self.assertEqual(len(self.make_catalog().access_tracker.warnings()), 1)

        self.catalog.record_conversation_result("free-2", "gpt-5.6-terra-wm")
        self.assertEqual(self.catalog.access_tracker.warnings(), [])


class ErrorResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_unavailable_becomes_403_with_chinese_reason(self) -> None:
        def handler(_body):
            raise ModelUnavailableError("模型 gpt-x 没有开放，请换一个模型", status_code=403, code="model_not_exposed")

        call = LoggedCall({"id": "k", "name": "k", "role": "user"}, "/v1/chat/completions", "gpt-x", "文本生成")
        with mock.patch.object(LoggedCall, "log"):
            response = await call.run(handler, {})

        self.assertEqual(response.status_code, 403)
        body = json.loads(response.body)
        self.assertEqual(body["error"]["code"], "model_not_exposed")
        self.assertIn("没有开放", body["error"]["message"])

    async def test_anthropic_shape_for_messages(self) -> None:
        def handler(_body):
            raise ModelUnavailableError("没有模型 x", status_code=400, code="model_not_found")

        call = LoggedCall({"id": "k", "name": "k", "role": "user"}, "/v1/messages", "x", "Messages")
        with mock.patch.object(LoggedCall, "log"):
            response = await call.run(handler, {}, sse="anthropic")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)["type"], "error")


class KeyModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.auth = AuthService(JSONStorageBackend(Path(self.temp_dir.name) / "accounts.json"))

    def test_allowed_models_round_trip_and_authenticate(self) -> None:
        item, raw_key = self.auth.create_key(role="user", name="search", allowed_models=["gpt-6", " gpt-6 ", "gpt-6-mini"])
        self.assertEqual(item["allowed_models"], ["gpt-6", "gpt-6-mini"])

        updated = self.auth.update_key(str(item["id"]), {"allowed_models": []}, role="user")
        self.assertEqual(updated["allowed_models"], [])
        self.auth.update_key(str(item["id"]), {"allowed_models": ["gpt-6"]}, role="user")
        identity = self.auth.authenticate(raw_key)
        self.assertEqual(identity["allowed_models"], ["gpt-6"])

    def test_empty_list_means_all_and_admin_is_unrestricted(self) -> None:
        self.assertIsNone(key_allowed_models({"role": "user", "allowed_models": []}))
        self.assertIsNone(key_allowed_models({"role": "admin", "allowed_models": ["gpt-6"]}))
        require_model_allowed({"role": "admin", "allowed_models": ["gpt-6"]}, "gpt-6-thinking")
        require_model_allowed({"role": "user", "allowed_models": ["gpt-6"]}, "gpt-6")
        with self.assertRaises(Exception) as caught:
            require_model_allowed({"role": "user", "allowed_models": ["gpt-6"]}, "gpt-6-mini")
        self.assertEqual(caught.exception.status_code, 403)


class KeyEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        install_exception_handlers(app)
        app.include_router(ai_api.create_router())
        self.client = TestClient(app)
        self.identity = {"id": "k1", "name": "search", "role": "user", "allowed_models": ["gpt-6", "gpt-6-mini"]}
        patcher = mock.patch.object(ai_api, "require_identity", side_effect=lambda _auth: self.identity)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_v1_models_is_filtered_by_key(self) -> None:
        with mock.patch.object(
            ai_api.openai_v1_models,
            "list_models",
            return_value=model_list(["gpt-6", "gpt-6-mini", "auto", "gpt-image-2"]),
        ):
            response = self.client.get("/v1/models", headers={"Authorization": "Bearer x"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["data"]], ["gpt-6", "gpt-6-mini"])

    def test_chat_with_disallowed_model_is_refused_before_upstream(self) -> None:
        with mock.patch.object(ai_api.openai_v1_chat_complete, "handle") as handle:
            response = self.client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer x"},
                json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "model_not_allowed_for_key")
        self.assertIn("不能使用模型 auto", response.json()["error"]["message"])
        handle.assert_not_called()

    def test_messages_and_images_check_the_model_too(self) -> None:
        messages = self.client.post(
            "/v1/messages",
            headers={"Authorization": "Bearer x"},
            json={"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]},
        )
        images = self.client.post(
            "/v1/images/generations",
            headers={"Authorization": "Bearer x"},
            json={"prompt": "cat", "model": "gpt-image-2"},
        )

        self.assertEqual(messages.status_code, 403)
        self.assertEqual(messages.json()["type"], "error")
        self.assertEqual(images.status_code, 403)

    def test_search_checks_the_configured_search_model(self) -> None:
        with mock.patch.object(ai_api.openai_search, "current_model", return_value="gpt-5-5"):
            response = self.client.post("/v1/search", headers={"Authorization": "Bearer x"}, json={"prompt": "news"})

        self.assertEqual(response.status_code, 403)
        self.assertIn("gpt-5-5", response.json()["error"]["message"])


class SearchModelSettingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = ModelRulesStore(Path(self.temp_dir.name) / "model_rules.json")

    def test_default_keeps_gpt_5_5_and_setting_persists(self) -> None:
        self.assertEqual(self.store.feature_model("search_model"), "gpt-5-5")
        self.assertEqual(self.store.feature_model("real_chat_model"), "auto")
        self.assertEqual(self.store.feature_model("daily_chat_model"), "auto")

        self.store.update_features({"search_model": "gpt-6-mini", "daily_chat_model": ""})
        reloaded = ModelRulesStore(Path(self.temp_dir.name) / "model_rules.json")
        self.assertEqual(reloaded.feature_model("search_model"), "gpt-6-mini")
        self.assertEqual(reloaded.feature_model("daily_chat_model"), "auto")
        with self.assertRaises(ValueError):
            self.store.update_features({"unknown_feature": "gpt-6"})

    def test_search_uses_configured_model_and_an_allowed_account(self) -> None:
        backend = mock.Mock()
        backend.search.return_value = {"answer": "ok"}
        with (
            mock.patch.object(openai_search, "model_rules_store", self.store),
            mock.patch.object(openai_search.account_service, "get_text_access_token", return_value="free-1") as selector,
            mock.patch.object(openai_search.account_service, "mark_text_used"),
            mock.patch.object(openai_search.account_service, "get_account", return_value={"email": "a@b.c"}),
            mock.patch.object(openai_search, "OpenAIBackendAPI", return_value=backend),
        ):
            self.store.update_features({"search_model": "gpt-6-mini"})
            result = openai_search.handle({"prompt": "news"})

        selector.assert_called_once_with(model="gpt-6-mini", internal=True)
        backend.search.assert_called_once_with("news", model="gpt-6-mini")
        self.assertEqual(result["_account_email"], "a@b.c")

    def test_search_without_logged_in_account_gives_clear_error(self) -> None:
        with (
            mock.patch.object(openai_search, "model_rules_store", self.store),
            mock.patch.object(openai_search.account_service, "get_text_access_token", return_value=""),
            self.assertRaisesRegex(ModelUnavailableError, "搜索需要登录账号"),
        ):
            openai_search.handle({"prompt": "news"})


class ModelsApiTests(PoolFixture):
    def setUp(self) -> None:
        super().setUp()
        app = FastAPI()
        app.include_router(models_api.create_router())
        self.client = TestClient(app)
        for patcher in (
            mock.patch.object(models_api, "require_admin", return_value={"role": "admin"}),
            mock.patch.object(models_api, "model_catalog_service", self.catalog),
            mock.patch.object(models_api.openai_v1_models, "list_models", side_effect=self.catalog.list_models),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_catalog_shape(self) -> None:
        data = self.client.get("/api/models/catalog").json()

        types = {item["type"]: item for item in data["account_types"]}
        self.assertEqual(types["free"]["accounts"], 2)
        self.assertEqual(types["free"]["label"], "Free")
        self.assertEqual(len(types["free"]["models"]), 14)
        self.assertEqual(len(types["Plus"]["models"]), 21)
        self.assertEqual(data["anonymous"]["models"], sorted(ANON_MODELS))
        self.assertEqual(data["summary"], {"exposed": 12, "hidden": 12, "no_account": 0})
        self.assertEqual(data["features"]["search_model"], "gpt-5-5")
        self.assertIn("gpt-5-5", data["key_model_options"])
        self.assertIsNotNone(data["refreshed_at"])

    def test_put_rules_and_features(self) -> None:
        response = self.client.put("/api/models/rules", json={
            "models": {"gpt-6-thinking": {"exposed": True, "account_types": ["plus"]}},
            "features": {"search_model": "gpt-6-mini"},
        })

        self.assertEqual(response.status_code, 200)
        view = {item["id"]: item for item in response.json()["models"]}
        self.assertEqual(view["gpt-6-thinking"]["account_types"], ["Plus"])
        self.assertEqual(view["gpt-6-thinking"]["result"], "exposed")
        self.assertEqual(response.json()["features"]["search_model"], "gpt-6-mini")

    def test_put_unknown_model_is_400(self) -> None:
        response = self.client.put("/api/models/rules", json={"models": {"gpt-9": {"exposed": True}}})

        self.assertEqual(response.status_code, 400)

    def test_refresh_refetches(self) -> None:
        self.client.get("/api/models/catalog")
        self.outcomes["plus-1"] = model_list([*PLUS_MODELS, "gpt-7-preview"])

        data = self.client.post("/api/models/refresh").json()

        self.assertIn("gpt-7-preview", {item["id"] for item in data["models"]})


if __name__ == "__main__":
    unittest.main()
