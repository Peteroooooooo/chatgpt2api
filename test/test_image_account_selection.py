from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth")

from services import account_service as account_service_module
from services.account_service import AccountService, ImageAccountsBusyError
from services.config import config
from services.image_rate_limit import (
    parse_reset_delay,
    rate_limit_from_message,
    rate_limit_from_sse_event,
    rate_limit_from_upstream_error,
)
from services.protocol import conversation
from services.protocol.conversation import ConversationRequest, ImageGenerationError, ImageOutput
from services.storage.json_storage import JSONStorageBackend
from utils.helper import UpstreamHTTPError

LIMIT_TEXT = (
    "You've hit the free plan limit for image generations requests. "
    "You can create more images when the limit resets in 2 hours and 5 minutes."
)
POLICY_TEXT = "非常抱歉，该提示可能违反了关于与第三方内容相似性的防护限制。如果你认为此判断有误，请重试或修改提示语。"


class _PoolCase(unittest.TestCase):
    """A real AccountService on a temp dir, no network, no log writes."""

    concurrency = 3
    wait_timeout = 5.0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.service = AccountService(JSONStorageBackend(Path(self._tmp.name) / "accounts.json"))
        # The live quota check before use just returns the stored account.
        self.service.fetch_remote_info = lambda token, event="fetch_remote_info", **_: self.service.get_account(token)
        patches = [
            patch.dict(config.data, {
                "image_account_concurrency": self.concurrency,
                "image_account_wait_timeout_secs": self.wait_timeout,
                "image_rate_limit_cooldown_secs": 300,
                "image_rate_limit_max_switches": 3,
                "auto_remove_rate_limited_accounts": False,
            }),
            patch.object(account_service_module, "log_service", MagicMock()),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self._tmp.cleanup)

    def add_accounts(self, *tokens: str, quota: int = 50) -> None:
        self.service.add_account_items(
            [{"access_token": token, "status": "正常", "quota": quota, "email": f"{token}@test"} for token in tokens]
        )

    def inflight(self) -> dict[str, int]:
        return dict(self.service._image_inflight)


class LeastInFlightTests(_PoolCase):
    def test_n_jobs_on_n_accounts_use_n_different_accounts(self) -> None:
        tokens = ["a", "b", "c", "d"]
        self.add_accounts(*tokens)
        picked = [self.service.get_available_access_token() for _ in tokens]
        self.assertEqual(sorted(picked), tokens)
        self.assertEqual(self.inflight(), {token: 1 for token in tokens})

    def test_one_extra_job_doubles_up_exactly_one_account(self) -> None:
        tokens = ["a", "b", "c", "d"]
        self.add_accounts(*tokens)
        for _ in range(len(tokens) + 1):
            self.service.get_available_access_token()
        loads = sorted(self.inflight().values())
        self.assertEqual(loads, [1, 1, 1, 2])

    def test_never_more_than_the_cap_and_fills_evenly(self) -> None:
        tokens = ["a", "b", "c"]
        self.add_accounts(*tokens)
        for count in range(1, 10):
            self.service.get_available_access_token()
            loads = self.inflight().values()
            self.assertLessEqual(max(loads) - min(loads), 1, f"uneven after {count} jobs")
            self.assertLessEqual(max(loads), 3)
        self.assertEqual(self.inflight(), {"a": 3, "b": 3, "c": 3})

    def test_idle_account_wins_over_round_robin_order(self) -> None:
        self.add_accounts("a", "b", "c")
        first = [self.service.get_available_access_token() for _ in range(3)]
        self.service.release_image_slot(first[1])
        # Only first[1] is idle now, so it must get the next job.
        self.assertEqual(self.service.get_available_access_token(), first[1])

    def test_round_robin_among_equally_free_accounts(self) -> None:
        self.add_accounts("a", "b", "c")
        order = []
        for _ in range(6):
            token = self.service.get_available_access_token()
            order.append(token)
            self.service.release_image_slot(token)
        self.assertEqual(order, ["a", "b", "c", "a", "b", "c"])

    def test_avoided_account_only_loses_ties(self) -> None:
        self.add_accounts("a", "b")
        self.assertEqual(self.service.get_available_access_token(avoid_tokens={"a"}), "b")
        # b now has a job and a is idle: load beats the avoid hint.
        self.assertEqual(self.service.get_available_access_token(avoid_tokens={"a"}), "a")

    def test_text_rotation_is_untouched_by_image_picks(self) -> None:
        self.add_accounts("a", "b")
        before = (dict(self.service._text_last_pick), self.service._text_pick_seq)
        for _ in range(4):
            self.service.release_image_slot(self.service.get_available_access_token())
        self.assertEqual((dict(self.service._text_last_pick), self.service._text_pick_seq), before)


class ReleaseTests(_PoolCase):
    def test_mark_image_result_releases_on_success_and_failure(self) -> None:
        self.add_accounts("a")
        token = self.service.get_available_access_token()
        self.service.mark_image_result(token, True)
        self.assertEqual(self.inflight(), {})
        token = self.service.get_available_access_token()
        self.service.mark_image_result(token, False)
        self.assertEqual(self.inflight(), {})

    def test_failed_live_check_releases_the_slot(self) -> None:
        self.add_accounts("a", "b")

        def flaky(token, event="fetch_remote_info", **_):
            if token == "a":
                raise RuntimeError("network down")
            return self.service.get_account(token)

        self.service.fetch_remote_info = flaky
        self.assertEqual(self.service.get_available_access_token(), "b")
        self.assertEqual(self.inflight(), {"b": 1})


class WaitTimeoutTests(_PoolCase):
    concurrency = 1
    wait_timeout = 0.4

    def test_all_accounts_full_waits_then_fails_busy(self) -> None:
        self.add_accounts("a")
        self.service.get_available_access_token()
        started = time.monotonic()
        with self.assertRaises(ImageAccountsBusyError) as ctx:
            self.service.get_available_access_token()
        waited = time.monotonic() - started
        self.assertGreaterEqual(waited, 0.35)
        self.assertLess(waited, 2.0)
        self.assertIn("busy", str(ctx.exception))
        self.assertNotIn("no available image quota", str(ctx.exception))
        self.assertEqual(self.inflight(), {"a": 1})

    def test_waiter_gets_the_slot_released_within_the_timeout(self) -> None:
        self.add_accounts("a")
        token = self.service.get_available_access_token()
        threading.Timer(0.1, self.service.release_image_slot, args=(token,)).start()
        self.assertEqual(self.service.get_available_access_token(), "a")

    def test_no_eligible_account_is_a_quota_error_not_a_wait(self) -> None:
        self.add_accounts("a", quota=0)
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "no available image quota"):
            self.service.get_available_access_token()
        self.assertLess(time.monotonic() - started, 0.3)


class ConcurrencyTests(_PoolCase):
    concurrency = 3

    def _acquire_in_threads(self, count: int) -> list[str]:
        barrier = threading.Barrier(count)
        picked: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            token = self.service.get_available_access_token()
            with lock:
                picked.append(token)

        threads = [threading.Thread(target=worker) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        return picked

    def test_parallel_jobs_spread_one_per_account(self) -> None:
        tokens = [f"t{i}" for i in range(6)]
        self.add_accounts(*tokens)
        picked = self._acquire_in_threads(6)
        self.assertEqual(sorted(picked), tokens)

    def test_parallel_jobs_fill_to_the_cap_evenly(self) -> None:
        tokens = [f"t{i}" for i in range(5)]
        self.add_accounts(*tokens)
        picked = self._acquire_in_threads(13)
        counts = Counter(picked)
        self.assertEqual(sorted(counts.values()), [2, 2, 3, 3, 3])
        self.assertLessEqual(max(self.inflight().values()), 3)


class CooldownTests(_PoolCase):
    def test_rate_limited_account_leaves_rotation_until_restore(self) -> None:
        self.add_accounts("a", "b")
        restore = datetime.now(timezone.utc) + timedelta(minutes=30)
        self.service.mark_image_rate_limited("a", reason="HTTP 429", restore_at=restore)
        account = self.service.get_account("a")
        self.assertEqual(account["status"], "限流")
        self.assertEqual(account["quota"], 50)
        self.assertTrue(account["restore_at"].startswith(restore.isoformat()[:16]))
        for _ in range(3):
            self.assertEqual(self.service.get_available_access_token(), "b")

    def test_default_cooldown_when_upstream_gives_no_reset(self) -> None:
        self.add_accounts("a")
        self.service.mark_image_rate_limited("a", reason="HTTP 429")
        until = AccountService._parse_time(self.service.get_account("a")["image_limited_until"])
        remaining = (until - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(290 <= remaining <= 300, remaining)

    def test_quota_refresh_does_not_end_the_cooldown_early(self) -> None:
        self.add_accounts("a")
        self.service.mark_image_rate_limited("a", reason="HTTP 429")
        held = self.service._hold_image_cooldown("a", {"status": "正常", "quota": 40})
        self.assertEqual(held["status"], "限流")
        self.service.update_account("a", held)
        self.assertEqual(self.service.get_account("a")["status"], "限流")

    def test_expired_cooldown_makes_the_account_a_candidate_again(self) -> None:
        self.add_accounts("a")
        self.service.mark_image_rate_limited("a", reason="HTTP 429")
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.service.update_account("a", {"image_limited_until": past})
        self.assertEqual(self.service._hold_image_cooldown("a", {"status": "正常"})["status"], "正常")
        self.assertEqual(self.service.get_available_access_token(), "a")

    def test_manual_status_change_clears_the_cooldown(self) -> None:
        self.add_accounts("a")
        self.service.mark_image_rate_limited("a", reason="HTTP 429")
        self.service.update_account("a", {"status": "正常"})
        account = self.service.get_account("a")
        self.assertNotIn("image_limited_until", account)
        self.assertEqual(self.service.get_available_access_token(), "a")

    def test_limit_message_zeroes_quota(self) -> None:
        self.add_accounts("a")
        self.service.mark_image_rate_limited("a", reason="limit text", quota_exhausted=True)
        self.assertEqual(self.service.get_account("a")["quota"], 0)


class LimitDetectionTests(unittest.TestCase):
    def test_limit_texts_are_recognised_with_reset_time(self) -> None:
        limit = rate_limit_from_message(LIMIT_TEXT)
        self.assertIsNotNone(limit)
        self.assertTrue(limit.quota_exhausted)
        delay = (limit.restore_at - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(7490 <= delay <= 7500, delay)
        self.assertIsNotNone(rate_limit_from_message("你已达到图片生成的使用上限，限制将在 3 小时后重置。"))
        self.assertIsNotNone(rate_limit_from_message("You've reached our limit of messages per hour. Please try again later."))

    def test_refusals_and_normal_replies_are_not_limits(self) -> None:
        self.assertIsNone(rate_limit_from_message(POLICY_TEXT))
        self.assertIsNone(rate_limit_from_message("This request violates our content policy."))
        self.assertIsNone(rate_limit_from_message("Here is a speed limit sign on a mountain road."))
        self.assertIsNone(rate_limit_from_message(""))

    def test_parse_reset_delay(self) -> None:
        self.assertEqual(parse_reset_delay("resets in 23 hours and 59 minutes"), 23 * 3600 + 59 * 60)
        self.assertEqual(parse_reset_delay("2 小时 5 分钟后重置"), 7500)
        self.assertIsNone(parse_reset_delay("try again later"))

    def test_http_429_on_account_calls(self) -> None:
        limit = rate_limit_from_upstream_error(
            UpstreamHTTPError("/backend-api/f/conversation", 429, {"detail": "Too many requests"}, retry_after=120)
        )
        self.assertIsNotNone(limit)
        self.assertFalse(limit.quota_exhausted)
        delay = (limit.restore_at - datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(110 <= delay <= 120, delay)
        cap = rate_limit_from_upstream_error(
            UpstreamHTTPError("chat_requirements_prepare", 429, {"detail": {"code": "model_cap_exceeded", "clears_in": 600}})
        )
        self.assertTrue(590 <= (cap.restore_at - datetime.now(timezone.utc)).total_seconds() <= 600)

    def test_429s_that_are_not_about_the_account(self) -> None:
        self.assertIsNone(rate_limit_from_upstream_error(UpstreamHTTPError("bootstrap", 429, "x")))
        self.assertIsNone(rate_limit_from_upstream_error(
            UpstreamHTTPError("/backend-api/f/conversation", 429, "<html>Error 1015 You are being rate limited</html>")
        ))
        self.assertIsNone(rate_limit_from_upstream_error(UpstreamHTTPError("/backend-api/f/conversation", 500, "x")))
        self.assertIsNone(rate_limit_from_upstream_error(UpstreamHTTPError("/backend-api/f/conversation", 403, "forbidden")))
        self.assertIsNone(rate_limit_from_upstream_error(ValueError("429")))

    def test_sse_error_event(self) -> None:
        self.assertIsNotNone(rate_limit_from_sse_event({"error": LIMIT_TEXT}))
        self.assertIsNotNone(rate_limit_from_sse_event({"type": "error", "error": {"code": "rate_limit_exceeded"}}))
        self.assertIsNone(rate_limit_from_sse_event({"error": None, "v": "x"}))
        self.assertIsNone(rate_limit_from_sse_event({"type": "error", "message": "something else broke"}))


class _FakeBackend:
    def __init__(self, access_token: str = "") -> None:
        self.access_token = access_token
        self.progress_callback = None

    def close(self) -> None:
        pass


class _GenerateCase(_PoolCase):
    """_generate_single_image with a fake pool and a fake upstream stream."""

    def setUp(self) -> None:
        super().setUp()
        self.behaviour: dict[str, str] = {}
        self.calls: list[str] = []
        patches = [
            patch.object(conversation, "account_service", self.service),
            patch.object(conversation, "OpenAIBackendAPI", _FakeBackend),
            patch.object(conversation, "stream_image_outputs", self._fake_stream),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _fake_stream(self, backend, request, index, total):
        token = backend.access_token
        self.calls.append(token)
        kind = self.behaviour.get(token, "ok")
        if kind == "429":
            raise UpstreamHTTPError("/backend-api/f/conversation", 429, {"detail": "Too many requests"}, retry_after=600)
        if kind == "limit_text":
            yield ImageOutput(kind="message", model=request.model, index=index, total=total, text=LIMIT_TEXT)
            return
        if kind == "policy":
            yield ImageOutput(kind="message", model=request.model, index=index, total=total, text=POLICY_TEXT)
            return
        if kind == "boom":
            raise ValueError("unexpected parser failure")
        if kind == "interrupt":
            raise KeyboardInterrupt()
        yield ImageOutput(kind="result", model=request.model, index=index, total=total, data=[{"b64_json": token}])

    def generate(self, **kwargs):
        request = ConversationRequest(model="gpt-image-2", prompt="a cat", **kwargs)
        return conversation._generate_single_image(request, 1, 1)


class GenerateSingleImageTests(_GenerateCase):

    def test_success_releases_slot(self) -> None:
        self.add_accounts("a")
        outputs = self.generate()
        self.assertEqual(outputs[0].data, [{"b64_json": "a"}])
        self.assertEqual(self.inflight(), {})
        self.assertEqual(self.service.get_account("a")["success"], 1)

    def test_unexpected_error_releases_slot_without_switching(self) -> None:
        self.add_accounts("a", "b")
        self.behaviour["a"] = "boom"
        with self.assertRaises(ImageGenerationError):
            self.generate()
        self.assertEqual(self.calls, ["a"])
        self.assertEqual(self.inflight(), {})
        self.assertEqual(self.service.get_account("a")["status"], "正常")

    def test_base_exception_still_releases_slot(self) -> None:
        self.add_accounts("a")
        self.behaviour["a"] = "interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.generate()
        self.assertEqual(self.inflight(), {})

    def test_429_at_start_switches_account_and_marks_limited(self) -> None:
        self.add_accounts("a", "b")
        self.behaviour["a"] = "429"
        outputs = self.generate()
        self.assertEqual(self.calls, ["a", "b"])
        self.assertEqual(outputs[0].data, [{"b64_json": "b"}])
        limited = self.service.get_account("a")
        self.assertEqual(limited["status"], "限流")
        until = AccountService._parse_time(limited["image_limited_until"])
        self.assertTrue(590 <= (until - datetime.now(timezone.utc)).total_seconds() <= 600)
        self.assertEqual(self.inflight(), {})
        # The next request does not touch the limited account.
        self.calls.clear()
        self.generate()
        self.assertEqual(self.calls, ["b"])

    def test_limit_message_switches_even_when_messages_are_errors(self) -> None:
        self.add_accounts("a", "b")
        self.behaviour["a"] = "limit_text"
        outputs = self.generate(message_as_error=True)
        self.assertEqual(self.calls, ["a", "b"])
        self.assertEqual(outputs[0].kind, "result")
        self.assertEqual(self.service.get_account("a")["quota"], 0)

    def test_every_account_limited_returns_quota_429(self) -> None:
        self.add_accounts("a", "b")
        self.behaviour.update({"a": "429", "b": "429"})
        with self.assertRaises(ImageGenerationError) as ctx:
            self.generate()
        self.assertIn("no available image quota", str(ctx.exception))
        self.assertEqual(sorted(self.calls), ["a", "b"])
        self.assertEqual(self.inflight(), {})
        response = conversation_error_response(ctx.exception)
        self.assertEqual(response.status_code, 429)

    def test_switch_cap_stops_the_chain(self) -> None:
        tokens = [f"t{i}" for i in range(6)]
        self.add_accounts(*tokens)
        self.behaviour.update({token: "429" for token in tokens})
        with patch.dict(config.data, {"image_rate_limit_max_switches": 2}):
            with self.assertRaises(ImageGenerationError) as ctx:
                self.generate()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("no available image quota", str(ctx.exception))

    def test_content_policy_refusal_does_not_switch(self) -> None:
        self.add_accounts("a", "b")
        self.behaviour["a"] = "policy"
        with self.assertRaises(ImageGenerationError) as ctx:
            self.generate(message_as_error=True)
        self.assertEqual(ctx.exception.code, "content_policy_violation")
        self.assertEqual(self.calls, ["a"])
        self.assertEqual(self.service.get_account("a")["status"], "正常")
        self.assertEqual(self.inflight(), {})

    def test_content_policy_message_is_returned_as_before(self) -> None:
        self.add_accounts("a", "b")
        self.behaviour["a"] = "policy"
        outputs = self.generate()
        self.assertEqual(self.calls, ["a"])
        self.assertEqual(outputs[0].kind, "message")


class BusyErrorTests(_GenerateCase):
    concurrency = 1
    wait_timeout = 0.2

    def test_busy_pool_returns_503_busy_error(self) -> None:
        self.add_accounts("a")
        self.service.get_available_access_token()  # another job holds the only slot
        with self.assertRaises(ImageGenerationError) as ctx:
            self.generate()
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.code, "image_accounts_busy")
        self.assertEqual(conversation_error_response(ctx.exception).status_code, 503)


def conversation_error_response(exc: Exception):
    from services.log_service import _image_error_response

    return _image_error_response(exc)


if __name__ == "__main__":
    unittest.main()
