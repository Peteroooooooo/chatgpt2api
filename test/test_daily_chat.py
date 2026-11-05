from __future__ import annotations

import random
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from services.daily_chat import CN_TZ, DailyChatService, spaced_times


def cn(hour: int, minute: int = 0, day: int = 6) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=CN_TZ)


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment.astimezone(timezone.utc)


class FakeAccounts:
    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self.accounts = accounts
        self.sent: list[str] = []
        self.status = "可用"

    def list_accounts(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.accounts]

    def test_real_chat(self, token: str) -> dict[str, Any]:
        self.sent.append(token)
        return {"status": self.status, "prompt": "hi", "prompt_id": "gen:en-explain:00000000", "conversation_url": "https://chatgpt.com/c/x"}


def accounts(count: int, **extra: Any) -> list[dict[str, Any]]:
    return [{"email": f"user{i}@example.com", "access_token": f"token-{i}", "status": "正常", **extra} for i in range(count)]


class SpacedTimesTests(unittest.TestCase):
    def test_times_stay_in_window_and_apart(self) -> None:
        start, end = cn(9), cn(23)
        times = spaced_times(start, end, 30, 600, random.Random(1))

        self.assertEqual(len(times), 30)
        self.assertEqual(times, sorted(times))
        self.assertGreaterEqual(times[0], start)
        self.assertLessEqual(times[-1], end)
        self.assertTrue(all((b - a).total_seconds() >= 300 - 1e-6 for a, b in zip(times, times[1:])))

    def test_gap_is_kept_when_window_is_too_small(self) -> None:
        times = spaced_times(cn(22), cn(23), 20, 600, random.Random(2))

        self.assertEqual(len(times), 7)
        self.assertLessEqual(times[-1], cn(23))
        self.assertTrue(all((b - a).total_seconds() >= 600 - 1e-6 for a, b in zip(times, times[1:])))


class DailyChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.clock = Clock(cn(0, 5))
        self.accounts = FakeAccounts(accounts(5))
        self.service = DailyChatService(
            self.accounts, root / "daily_chat.json", root / "plans", now=self.clock, rng=random.Random(7)
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def pending(self) -> list[dict[str, Any]]:
        plan = self.service.load_plan(self.service.today()) or {}
        return [item for item in plan.get("items", []) if item["state"] == "pending"]

    def item_for(self, email: str) -> dict[str, Any]:
        plan = self.service.load_plan(self.service.today()) or {}
        return next(item for item in plan["items"] if item["email"] == email)

    def test_plan_gives_each_account_one_spaced_time_inside_the_window(self) -> None:
        self.accounts.accounts.append({"email": "off@example.com", "access_token": "t-off", "status": "禁用"})

        plan = self.service.ensure_plan()

        times = sorted(datetime.fromisoformat(item["scheduled_at"]) for item in self.pending())
        self.assertEqual(self.service.get_settings()["min_gap_minutes"], 5)
        self.assertEqual(len(times), 5)
        self.assertGreaterEqual(times[0], cn(9))
        self.assertLessEqual(times[-1], cn(23))
        self.assertTrue(all((b - a).total_seconds() >= 300 - 1e-6 for a, b in zip(times, times[1:])))
        self.assertEqual(self.item_for("off@example.com")["state"], "skipped")
        self.assertEqual(plan["date"], "2026-11-06")

    def test_plan_is_persisted_and_not_rebuilt(self) -> None:
        first = self.service.ensure_plan()
        self.clock.moment = cn(8)

        second = self.service.ensure_plan()

        self.assertEqual(first["items"], second["items"])

    def test_plan_built_mid_window_starts_after_now(self) -> None:
        self.clock.moment = cn(17)

        self.service.ensure_plan()

        self.assertTrue(all(datetime.fromisoformat(item["scheduled_at"]) > cn(17) for item in self.pending()))

    def test_plan_built_after_window_skips_everyone(self) -> None:
        self.clock.moment = cn(23, 30)

        plan = self.service.ensure_plan()

        self.assertEqual({item["skip_kind"] for item in plan["items"]}, {"window"})

    def test_tick_sends_one_due_item_and_records_result(self) -> None:
        self.service.ensure_plan()
        first = min(self.pending(), key=lambda item: item["scheduled_at"])
        self.clock.moment = datetime.fromisoformat(first["scheduled_at"]) + timedelta(seconds=5)

        self.service.tick()
        self.service.tick()

        self.assertEqual(self.accounts.sent, [f"token-{first['email'][4]}"])
        done = self.item_for(first["email"])
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["result"], "可用")
        self.assertEqual(len(self.pending()), 4)

    def test_failed_send_is_not_retried_the_same_day(self) -> None:
        self.service.ensure_plan()
        self.accounts.status = "测试失败"
        first = min(self.pending(), key=lambda item: item["scheduled_at"])
        self.clock.moment = datetime.fromisoformat(first["scheduled_at"]) + timedelta(seconds=5)
        self.service.tick()
        sent = len(self.accounts.sent)

        self.service.reschedule_today()
        self.service.tick()

        self.assertEqual(len(self.accounts.sent), sent)
        self.assertEqual(self.item_for(first["email"])["result"], "测试失败")

    def test_missed_items_get_new_spaced_times_instead_of_a_burst(self) -> None:
        self.service.ensure_plan()
        last = max(datetime.fromisoformat(item["scheduled_at"]) for item in self.pending())
        self.clock.moment = min(last - timedelta(minutes=1), cn(20))

        self.service.tick()

        self.assertEqual(self.accounts.sent, [])
        pending = self.pending()
        self.assertTrue(all(item.get("rescheduled") for item in pending))
        times = sorted(datetime.fromisoformat(item["scheduled_at"]) for item in pending)
        self.assertGreater(times[0], self.clock.moment)
        self.assertTrue(all((b - a).total_seconds() >= 300 - 1e-6 for a, b in zip(times, times[1:])))

    def test_missed_items_after_window_are_skipped(self) -> None:
        self.service.ensure_plan()
        self.clock.moment = cn(23, 30)

        self.service.tick()

        self.assertEqual(self.accounts.sent, [])
        self.assertEqual(self.pending(), [])

    def test_send_time_check_skips_broken_accounts_but_sends_limited_ones(self) -> None:
        self.service.ensure_plan()
        order = sorted(self.pending(), key=lambda item: item["scheduled_at"])
        by_email = {item["email"]: item for item in self.accounts.accounts}
        by_email[order[0]["email"]]["status"] = "异常"
        by_email[order[1]["email"]]["status"] = "限流"
        by_email[order[2]["email"]]["relogin_paused"] = True

        for item in order[:3]:
            self.clock.moment = datetime.fromisoformat(item["scheduled_at"]) + timedelta(seconds=1)
            self.service.tick()

        self.assertEqual(self.item_for(order[0]["email"])["reason"], "账号异常")
        self.assertEqual(self.item_for(order[1]["email"])["result"], "可用")
        self.assertEqual(self.item_for(order[2]["email"])["state"], "skipped")
        self.assertEqual(len(self.accounts.sent), 1)

    def test_startup_closes_interrupted_sends_and_yesterdays_leftovers(self) -> None:
        self.service.ensure_plan()
        first = min(self.pending(), key=lambda item: item["scheduled_at"])
        self.clock.moment = datetime.fromisoformat(first["scheduled_at"])
        claimed = self.service.take_due_item(self.service.get_settings())
        self.assertIsNotNone(claimed)

        self.clock.moment = cn(1, 0, day=7)
        self.service.close_stale(startup=True)

        yesterday = self.service.load_plan("2026-11-06") or {}
        kinds = {item["email"]: item.get("skip_kind") for item in yesterday["items"]}
        self.assertEqual(kinds[first["email"]], "interrupted")
        self.assertEqual({kind for email, kind in kinds.items() if email != first["email"]}, {"window"})

    def test_reschedule_adds_new_accounts_and_keeps_sent_ones(self) -> None:
        self.service.ensure_plan()
        first = min(self.pending(), key=lambda item: item["scheduled_at"])
        self.clock.moment = datetime.fromisoformat(first["scheduled_at"])
        self.service.tick()
        self.accounts.accounts.append({"email": "new@example.com", "access_token": "t-new", "status": "正常"})

        plan = self.service.reschedule_today()

        self.assertEqual(len(plan["items"]), 6)
        self.assertEqual(self.item_for(first["email"])["state"], "done")
        self.assertEqual(self.item_for("new@example.com")["state"], "pending")

    def test_run_now_sends_a_pending_item_once(self) -> None:
        self.service.ensure_plan()
        item = self.pending()[0]

        self.service.run_now(item["id"])
        for _ in range(50):
            if self.item_for(item["email"])["state"] == "done":
                break
            time.sleep(0.02)

        self.assertEqual(self.item_for(item["email"])["trigger"], "manual")
        with self.assertRaises(ValueError):
            self.service.run_now(item["id"])

    def test_lookup_error_still_closes_the_item(self) -> None:
        self.service.ensure_plan()
        first = min(self.pending(), key=lambda item: item["scheduled_at"])
        self.clock.moment = datetime.fromisoformat(first["scheduled_at"])

        def broken() -> list[dict[str, Any]]:
            raise RuntimeError("storage offline")

        self.accounts.list_accounts = broken  # type: ignore[method-assign]
        self.service.take_due_item(self.service.get_settings())
        self.service._send("2026-11-06", first, "schedule")

        item = self.item_for(first["email"])
        self.assertEqual(item["state"], "done")
        self.assertIn("storage offline", item["error"])

    def test_startup_closes_plans_from_several_days_ago(self) -> None:
        self.service.ensure_plan()
        self.clock.moment = cn(10, 0, day=9)

        self.service.close_stale(startup=True)

        old = self.service.load_plan("2026-11-06") or {}
        self.assertEqual({item["state"] for item in old["items"]}, {"skipped"})

    def test_account_without_email_keeps_its_key_after_token_change(self) -> None:
        account = {"access_token": "old-token", "user_id": "user-abc", "status": "正常"}
        renewed = {**account, "access_token": "new-token"}

        self.assertEqual(DailyChatService.account_key(account), DailyChatService.account_key(renewed))

    def test_late_plan_keeps_the_gap_and_skips_what_does_not_fit(self) -> None:
        self.accounts.accounts = accounts(10)
        self.clock.moment = cn(22, 39)

        plan = self.service.ensure_plan()

        pending = self.pending()
        times = sorted(datetime.fromisoformat(item["scheduled_at"]) for item in pending)
        self.assertEqual(len(pending), 5)
        self.assertTrue(all((b - a).total_seconds() >= 300 - 1e-6 for a, b in zip(times, times[1:])))
        skipped = [item for item in plan["items"] if item["state"] == "skipped"]
        self.assertEqual(len(skipped), 5)
        self.assertTrue(all(item["skip_kind"] == "window" for item in skipped))

    def test_settings_reject_an_empty_window(self) -> None:
        with self.assertRaises(ValueError):
            self.service.update_settings({"window_start": "20:00", "window_end": "09:00"})

        saved = self.service.update_settings({"window_start": "10:30", "min_gap_minutes": 15})
        self.assertEqual(saved["window_start"], "10:30")
        self.assertEqual(saved["min_gap_minutes"], 15)


if __name__ == "__main__":
    unittest.main()
