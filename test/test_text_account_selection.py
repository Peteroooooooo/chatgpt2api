from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth")

from services import account_service as account_service_module
from services import log_service as log_service_module
from services.account_service import AccountService
from services.config import config
from services.log_service import LoggedCall
from services.model_service import ModelRoute
from services.openai_backend_api import InvalidAccessTokenError
from services.protocol import (
    anthropic_v1_messages,
    conversation,
    openai_search,
    openai_v1_chat_complete,
    openai_v1_response,
)
from services.protocol.chat_completion_cache import chat_completion_cache
from services.protocol.conversation import ConversationRequest
from services.storage.json_storage import JSONStorageBackend

ANY_ROUTE = ModelRoute(account_types=frozenset(), any_account=True)
OLD = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")


def fake_jwt(exp_in_seconds: int, tag: str) -> str:
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': int(time.time()) + exp_in_seconds, 'jti': tag})}.sig"


class _ServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "accounts.json"
        self.service = AccountService(JSONStorageBackend(self.path))
        patches = [
            patch.object(account_service_module, "log_service", MagicMock()),
            patch.dict(config.data, {"auto_remove_invalid_accounts": False, "invalid_after_renewal_checks": 2}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def add(self, *items: dict) -> None:
        self.service.add_account_items([
            {"status": "正常", "quota": 10, "created_at": OLD, "email": f"{item['access_token']}@test", **item}
            for item in items
        ])


class _FakeBackend:
    """Stands in for OpenAIBackendAPI in fetch_remote_info: tokens in `dead` get a 401 from /me."""

    dead: set[str] = set()

    def __init__(self, access_token: str = "", *args, **kwargs) -> None:
        self.access_token = access_token

    def get_user_info(self) -> dict:
        if self.access_token in self.dead:
            raise InvalidAccessTokenError("token invalidated (/backend-api/me)")
        return {"status": "正常", "quota": 10}

    def close(self) -> None:
        pass


class DeadTokenAfterRenewalTests(_ServiceCase):
    def setUp(self) -> None:
        super().setUp()
        _FakeBackend.dead = set()
        backend_patch = patch("services.openai_backend_api.OpenAIBackendAPI", _FakeBackend)
        backend_patch.start()
        self.addCleanup(backend_patch.stop)
        self.renewals: list[str] = []

    def renew_with(self, next_token):
        def fake_refresh(refresh_token, account=None):
            current = str((account or {}).get("access_token") or "")
            new_token = next_token(current)
            self.renewals.append(new_token)
            return {"access_token": new_token, "refresh_token": refresh_token, "id_token": ""}

        self.service._request_access_token_refresh = fake_refresh

    def check(self, token: str) -> None:
        try:
            self.service.fetch_remote_info(token, "refresh_accounts")
        except InvalidAccessTokenError:
            pass

    def age_streak(self, token: str, seconds: int = 300) -> None:
        account = self.service.get_account(token) or {}
        since = self.service._parse_time(account.get("invalid_after_renewal_since"))
        self.assertIsNotNone(since)
        self.service.update_account(token, {"invalid_after_renewal_since": (since - timedelta(seconds=seconds)).isoformat()}, quiet=True)

    def test_same_token_renewal_marks_account_abnormal_after_two_checks(self) -> None:
        self.add({"access_token": "dead", "refresh_token": "rt"})
        _FakeBackend.dead = {"dead"}
        self.renew_with(lambda current: current)

        self.check("dead")
        first = self.service.get_account("dead")
        self.assertEqual(first["status"], "正常")
        self.assertEqual(first["invalid_count"], 1)
        self.assertEqual(first["invalid_after_renewal_count"], 1)

        self.age_streak("dead")
        self.check("dead")
        second = self.service.get_account("dead")
        self.assertEqual(second["status"], "异常")
        self.assertEqual(second["quota"], 0)
        # The same-token renewal no longer wipes the count it is supposed to build up.
        self.assertEqual(second["invalid_count"], 2)
        self.assertEqual(self.renewals, ["dead", "dead"])
        # auto_remove_invalid_accounts is off: the account stays in the pool.
        self.assertIn("dead", self.service.list_tokens())

    def test_quick_repeat_checks_do_not_confirm_a_dead_token(self) -> None:
        self.add({"access_token": "dead", "refresh_token": "rt"})
        _FakeBackend.dead = {"dead"}
        self.renew_with(lambda current: current)
        self.check("dead")
        self.check("dead")  # e.g. two image picks seconds apart
        account = self.service.get_account("dead")
        self.assertEqual(account["invalid_after_renewal_count"], 2)
        self.assertEqual(account["status"], "正常")

    def test_new_account_keeps_its_grace_period(self) -> None:
        self.add({"access_token": "fresh", "refresh_token": "rt", "created_at": AccountService._now()})
        _FakeBackend.dead = {"fresh"}
        self.renew_with(lambda current: current)
        self.check("fresh")
        self.age_streak("fresh")
        self.check("fresh")
        self.assertEqual(self.service.get_account("fresh")["status"], "正常")

    def test_rotated_working_token_resets_the_counts(self) -> None:
        self.add({"access_token": "old", "refresh_token": "rt", "invalid_count": 1})
        _FakeBackend.dead = {"old"}
        self.renew_with(lambda current: "new")
        self.check("old")
        account = self.service.get_account("new")
        self.assertEqual(account["access_token"], "new")
        self.assertEqual(account["status"], "正常")
        self.assertEqual(account["invalid_count"], 0)
        self.assertEqual(account["invalid_after_renewal_count"], 0)
        self.assertIsNone(account["last_invalid_at"])

    def test_rotated_but_rejected_tokens_still_end_the_loop(self) -> None:
        self.add({"access_token": "t0", "refresh_token": "rt"})
        counter = iter(range(1, 100))

        def next_token(current: str) -> str:
            token = f"t{next(counter)}"
            _FakeBackend.dead.add(token)
            return token

        _FakeBackend.dead = {"t0"}
        self.renew_with(next_token)
        self.check("t0")
        token = self.service.resolve_access_token("t0")
        self.assertEqual(self.service.get_account(token)["status"], "正常")
        self.age_streak(token)
        self.check(token)
        token = self.service.resolve_access_token(token)
        self.assertEqual(self.service.get_account(token)["status"], "异常")

    def test_successful_check_clears_the_streak(self) -> None:
        self.add({"access_token": "flaky", "refresh_token": "rt"})
        _FakeBackend.dead = {"flaky"}
        self.renew_with(lambda current: current)
        self.check("flaky")
        _FakeBackend.dead = set()
        self.check("flaky")
        account = self.service.get_account("flaky")
        self.assertEqual(account["invalid_after_renewal_count"], 0)
        self.assertEqual(account["invalid_count"], 0)
        self.assertEqual(account["status"], "正常")

    def test_dead_account_is_not_renewed_again_by_the_watcher(self) -> None:
        dead = fake_jwt(3600, "dead")
        alive = fake_jwt(3600, "alive")
        self.add({"access_token": dead, "refresh_token": "rt"}, {"access_token": alive, "refresh_token": "rt2"})
        self.assertEqual(sorted(self.service.list_expiring_access_tokens()), sorted([dead, alive]))
        self.service.update_account(dead, {"status": "异常", "invalid_after_renewal_count": 2}, quiet=True)
        self.assertEqual(self.service.list_expiring_access_tokens(), [alive])
        # Other 异常 accounts keep being renewed (a new token can bring them back).
        self.service.update_account(alive, {"status": "异常"}, quiet=True)
        self.assertEqual(self.service.list_expiring_access_tokens(), [alive])

    def test_relogin_brings_a_dead_account_back(self) -> None:
        self.add({"access_token": "dead", "refresh_token": "rt", "status": "异常", "invalid_count": 3,
                  "invalid_after_renewal_count": 2, "invalid_after_renewal_since": OLD})
        self.service._login_with_password = lambda email, password, push_wait=0: {
            "ok": True, "access_token": "fresh", "refresh_token": "rt-new", "id_token": "", "source_type": "password",
        }
        self.service._password_re_login_body("dead", "dead@test", "pw", "manual_relogin")
        account = self.service.get_account("fresh")
        self.assertEqual(account["status"], "正常")
        self.assertEqual(account["invalid_count"], 0)
        self.assertEqual(account["invalid_after_renewal_count"], 0)
        self.assertIsNone(account["invalid_after_renewal_since"])


class TextLeastRecentlyPickedTests(_ServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service.refresh_access_token = lambda token, **_kwargs: token
        route_patch = patch("services.model_service.model_catalog_service.resolve_route", return_value=ANY_ROUTE)
        route_patch.start()
        self.addCleanup(route_patch.stop)

    def pick(self, **kwargs) -> str:
        return self.service.get_text_access_token(**kwargs)

    def test_picks_spread_evenly(self) -> None:
        self.add(*({"access_token": token} for token in "abcd"))
        counts = Counter(self.pick() for _ in range(400))
        self.assertEqual(counts, Counter({token: 100 for token in "abcd"}))

    def test_renewal_moving_an_account_to_the_end_keeps_its_turn(self) -> None:
        self.add(*({"access_token": token} for token in "abcd"))
        self.assertEqual([self.pick() for _ in range(4)], ["a", "b", "c", "d"])
        # A renewal re-inserts the account at the end of the list under a new token.
        self.service._apply_refreshed_tokens("a", {"access_token": "a2"}, "test")
        self.assertEqual(self.service.list_tokens()[-1], "a2")
        self.assertEqual([self.pick() for _ in range(4)], ["a2", "b", "c", "d"])

    def test_varying_candidate_sets_stay_fair(self) -> None:
        self.add(
            {"access_token": "f1", "type": "free"},
            {"access_token": "f2", "type": "free"},
            {"access_token": "p1", "type": "Plus"},
        )
        free_only = ModelRoute(account_types=frozenset({"free"}))
        counts: Counter[str] = Counter()
        with patch("services.model_service.model_catalog_service.resolve_route") as route:
            for step in range(300):
                # Alternate between a model every account runs and one only free accounts run.
                route.return_value = ANY_ROUTE if step % 2 else free_only
                counts[self.pick(model="x")] += 1
        # Free-only calls never reach p1, so it gets fewer picks; the free accounts share evenly.
        self.assertLessEqual(abs(counts["f1"] - counts["f2"]), 1)
        self.assertEqual(sum(counts.values()), 300)
        self.assertGreater(counts["p1"], 0)

    def test_excluded_accounts_are_skipped_even_after_renewal(self) -> None:
        self.add(*({"access_token": token} for token in "abc"))
        self.service._apply_refreshed_tokens("a", {"access_token": "a2"}, "test")
        # The request tried "a" before it was renewed to "a2": it must not land on it again.
        picked = {self.pick(excluded_tokens={"a", "b"}) for _ in range(5)}
        self.assertEqual(picked, {"c"})

    def test_disabled_and_abnormal_accounts_are_not_picked(self) -> None:
        self.add({"access_token": "ok"}, {"access_token": "off", "status": "禁用"}, {"access_token": "bad", "status": "异常"})
        self.assertEqual({self.pick() for _ in range(5)}, {"ok"})

    def test_restart_starts_from_last_used_at(self) -> None:
        self.add(
            {"access_token": "recent", "last_used_at": "2026-11-10 10:00:00"},
            {"access_token": "never"},
            {"access_token": "oldest", "last_used_at": "2026-11-01 08:00:00"},
            {"access_token": "middle", "last_used_at": "2026-11-05 08:00:00"},
        )
        restarted = AccountService(JSONStorageBackend(self.path))
        restarted.refresh_access_token = lambda token, **_kwargs: token
        order = [restarted.get_text_access_token() for _ in range(4)]
        self.assertEqual(order, ["never", "oldest", "middle", "recent"])

    def test_new_account_goes_first(self) -> None:
        self.add(*({"access_token": token} for token in "ab"))
        self.pick(), self.pick()
        self.add({"access_token": "new"})
        self.assertEqual(self.pick(), "new")

    def test_merge_by_user_id_moves_pick_records(self) -> None:
        self.service.fetch_remote_info = lambda token, event="", **_: self.service.get_account(token)
        self.add({"access_token": "a", "user_id": "u1"}, {"access_token": "b", "user_id": "u2"})
        self.assertEqual([self.pick(), self.pick()], ["a", "b"])
        with patch.dict(config.data, {"image_account_concurrency": 3}):
            image_token = self.service.get_available_access_token()
            self.service.release_image_slot(image_token)
        self.assertEqual(image_token, "a")
        # Session JSON import of the same user under a rotated token.
        self.service.add_account_items([{"access_token": "a-new", "user_id": "u1", "merge_by_user_id": True}])
        self.assertNotIn("a", self.service._text_last_pick)
        self.assertNotIn("a", self.service._image_last_pick)
        self.assertIn("a-new", self.service._text_last_pick)
        self.assertIn("a-new", self.service._image_last_pick)
        # a-new was picked before b, so the merged account waits for its turn.
        self.assertEqual(self.pick(), "a-new")
        with patch.dict(config.data, {"image_account_concurrency": 3}):
            self.assertEqual(self.service.get_available_access_token(), "b")

    def test_delete_drops_pick_records(self) -> None:
        self.add(*({"access_token": token} for token in "ab"))
        self.pick()
        self.service.delete_accounts(["a"])
        self.assertNotIn("a", self.service._text_last_pick)


class TextFallbackTests(_ServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service.refresh_access_token = lambda token, **_kwargs: token
        for item in (
            patch("services.model_service.model_catalog_service.resolve_route", return_value=ANY_ROUTE),
            patch.object(conversation, "account_service", self.service),
            patch.object(conversation, "OpenAIBackendAPI", lambda access_token="": MagicMock(access_token=access_token)),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.add(*({"access_token": token} for token in "abc"))
        self.used: list[str] = []

    def fake_events(self, broken: set[str]):
        def events(backend, **_kwargs):
            self.used.append(backend.access_token)
            if backend.access_token in broken:
                raise RuntimeError("token_invalidated")
            yield {"type": "conversation.delta", "delta": f"hi from {backend.access_token}"}

        return events

    def test_fallback_moves_to_an_untried_account_and_reports_it(self) -> None:
        with patch.object(conversation, "conversation_events", self.fake_events({"a", "b"})):
            request = ConversationRequest(messages=[{"role": "user", "content": "x"}])
            backend = MagicMock(access_token=self.service.get_text_access_token())
            text = "".join(conversation.stream_text_deltas(backend, request))
        self.assertEqual(self.used, ["a", "b", "c"])
        self.assertEqual(text, "hi from c")
        self.assertEqual(request.account_email, "c@test")
        self.assertEqual(request.tried_account_emails, ["a@test", "b@test"])

    def test_error_names_the_last_account_tried(self) -> None:
        with patch.object(conversation, "conversation_events", self.fake_events({"a", "b", "c"})):
            request = ConversationRequest(messages=[{"role": "user", "content": "x"}])
            backend = MagicMock(access_token=self.service.get_text_access_token())
            with self.assertRaises(RuntimeError) as ctx:
                list(conversation.stream_text_deltas(backend, request))
        self.assertEqual(sorted(self.used), ["a", "b", "c"])
        self.assertEqual(getattr(ctx.exception, "account_email", ""), "c@test")
        self.assertEqual(getattr(ctx.exception, "tried_account_emails", []), ["a@test", "b@test"])


class CallLogAccountTests(_ServiceCase):
    """Every text-type call log entry names the account that served it."""

    def setUp(self) -> None:
        super().setUp()
        self.service.refresh_access_token = lambda token, **_kwargs: token
        self.add({"access_token": "tok-1", "email": "alice@example.com"})
        self.logged: list[dict] = []
        cache_off = {"enabled": False, "ttl_seconds": 0, "max_entries": 1, "stream_cache": False, "dedupe_inflight": False}
        for item in (
            patch("services.model_service.model_catalog_service.resolve_route", return_value=ANY_ROUTE),
            patch("services.model_service.model_catalog_service.record_conversation_result"),
            patch.object(conversation, "account_service", self.service),
            patch.object(anthropic_v1_messages, "account_service", self.service),
            patch.object(openai_search, "account_service", self.service),
            patch.object(conversation, "OpenAIBackendAPI", lambda access_token="": MagicMock(access_token=access_token)),
            patch.object(anthropic_v1_messages, "OpenAIBackendAPI", lambda access_token="": MagicMock(access_token=access_token)),
            patch.object(conversation, "conversation_events", self.events),
            patch.object(log_service_module.log_service, "add", side_effect=self.capture),
            patch.object(chat_completion_cache, "_settings", return_value=cache_off),
        ):
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def events(backend, **_kwargs):
        yield {"type": "conversation.delta", "delta": "Hello"}
        yield {"type": "conversation.delta", "delta": " there"}

    def capture(self, type, summary="", detail=None, **_):
        self.logged.append({"type": type, "summary": summary, "detail": detail or {}})

    def call(self, endpoint: str, handler, body: dict, sse: str = "openai"):
        call = LoggedCall({"id": "k", "name": "key", "role": "admin"}, endpoint, "auto", endpoint)
        if body.get("stream"):
            items = list(call.stream(handler(body)))
        else:
            items = [asyncio.run(call.run(handler, body, sse=sse))]
        detail = [entry["detail"] for entry in self.logged if entry["type"] == "call"][-1]
        return items, detail

    def assert_served_by_alice(self, items, detail) -> None:
        self.assertEqual(detail.get("account_email"), "alice@example.com")
        self.assertNotIn("_account_email", json.dumps(items, ensure_ascii=False))

    def test_chat_completion(self) -> None:
        body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
        self.assert_served_by_alice(*self.call("/v1/chat/completions", openai_v1_chat_complete.handle, body))

    def test_chat_completion_stream(self) -> None:
        body = {"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
        self.assert_served_by_alice(*self.call("/v1/chat/completions", openai_v1_chat_complete.handle, body))

    def test_responses(self) -> None:
        body = {"model": "auto", "input": "hi"}
        self.assert_served_by_alice(*self.call("/v1/responses", openai_v1_response.handle, body))

    def test_responses_stream(self) -> None:
        body = {"model": "auto", "stream": True, "input": "hi"}
        self.assert_served_by_alice(*self.call("/v1/responses", openai_v1_response.handle, body))

    def test_anthropic_messages(self) -> None:
        body = {"model": "auto", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]}
        self.assert_served_by_alice(*self.call("/v1/messages", anthropic_v1_messages.handle, body, sse="anthropic"))

    def test_anthropic_messages_stream(self) -> None:
        body = {"model": "auto", "stream": True, "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]}
        self.assert_served_by_alice(*self.call("/v1/messages", anthropic_v1_messages.handle, body, sse="anthropic"))

    def test_search(self) -> None:
        class SearchBackend:
            def __init__(self, token):
                self.token = token

            def search_events(self, prompt, model=None):
                yield {"type": "delta", "text": "answer"}
                yield {"type": "done", "result": {"answer": "answer", "sources": []}}

            def close(self):
                pass

        with patch.object(openai_search, "OpenAIBackendAPI", SearchBackend):
            items, detail = self.call("/v1/search", openai_search.handle, {"prompt": "q", "model": "auto"})
            self.assert_served_by_alice(items, detail)
            items, detail = self.call(
                "/v1/chat/completions",
                openai_v1_chat_complete.handle,
                {"model": "auto", "stream": True, "tools": [{"type": "web_search"}], "messages": [{"role": "user", "content": "q"}]},
            )
            self.assert_served_by_alice(items, detail)

    def test_fallback_logs_final_and_tried_accounts(self) -> None:
        self.add({"access_token": "tok-2", "email": "bob@example.com"})

        def events(backend, **_kwargs):
            if backend.access_token == "tok-1":
                raise RuntimeError("token_invalidated")
            yield {"type": "conversation.delta", "delta": "ok"}

        with patch.object(conversation, "conversation_events", events):
            body = {"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
            # tok-1 goes first (both never used, tok-1 is earlier in the list).
            _items, detail = self.call("/v1/chat/completions", openai_v1_chat_complete.handle, body)
        self.assertEqual(detail.get("account_email"), "bob@example.com")
        self.assertEqual(detail.get("tried_account_emails"), ["alice@example.com"])

    def test_failed_call_names_the_account(self) -> None:
        def events(backend, **_kwargs):
            raise RuntimeError("upstream exploded")
            yield  # pragma: no cover

        with patch.object(conversation, "conversation_events", events):
            body = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
            _items, detail = self.call("/v1/chat/completions", openai_v1_chat_complete.handle, body)
        self.assertEqual(detail.get("status"), "failed")
        self.assertEqual(detail.get("account_email"), "alice@example.com")

    def test_cache_hit_is_not_attributed_to_an_account(self) -> None:
        cache_on = {"enabled": True, "ttl_seconds": 60, "max_entries": 8, "stream_cache": True, "dedupe_inflight": True}
        chat_completion_cache.clear()
        self.addCleanup(chat_completion_cache.clear)
        with patch.object(chat_completion_cache, "_settings", return_value=cache_on):
            body = {"model": "auto", "messages": [{"role": "user", "content": "cache me"}]}
            _items, first = self.call("/v1/chat/completions", openai_v1_chat_complete.handle, body)
            items, second = self.call("/v1/chat/completions", openai_v1_chat_complete.handle, body)
        self.assertEqual(first.get("account_email"), "alice@example.com")
        self.assertNotIn("account_email", second)
        self.assertNotIn("_account_email", json.dumps(items, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
