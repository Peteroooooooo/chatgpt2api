"""Daily real-chat: every account sends one real conversation per day at a random time.

Each Beijing-time day gets a plan file listing one item per account with a random,
spaced send time inside the configured window. A scheduler thread sends due items
one at a time through AccountService.test_real_chat, so the result also lands on the
account's real-chat column. Items that could not run in time (server down, window
over) are pushed later while room remains, otherwise skipped. No same-day retry.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable

from services.config import DATA_DIR
from utils.helper import anonymize_token

CN_TZ = timezone(timedelta(hours=8))
SETTINGS_FILE = DATA_DIR / "daily_chat.json"
PLAN_DIR = DATA_DIR / "daily_chat"
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "window_start": "09:00",
    "window_end": "23:00",
    "min_gap_minutes": 5,
    "include_limited": True,
    "keep_days": 30,
}
TICK_SECONDS = 20.0
MISSED_AFTER_MINUTES = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_hhmm(value: object, fallback: str) -> str:
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        if 0 <= hour <= 24 and 0 <= minute < 60 and hour * 60 + minute <= 24 * 60:
            return f"{hour:02d}:{minute:02d}"
    except Exception:
        pass
    return fallback


def _at(day: date, hhmm: str) -> datetime:
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    return datetime(day.year, day.month, day.day, tzinfo=CN_TZ) + timedelta(hours=hour, minutes=minute)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse_iso(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def spaced_times(start: datetime, end: datetime, count: int, gap_seconds: float, rng: random.Random) -> list[datetime]:
    """Random sorted times in [start, end], neighbours at least gap apart.

    The gap is never shortened: if the window cannot hold `count` items, only as many
    times as fit are returned.
    """

    available = max(0.0, (end - start).total_seconds())
    if count <= 0:
        return []
    fit = count if gap_seconds <= 0 else min(count, int(available // gap_seconds) + 1)
    slack = max(0.0, available - gap_seconds * (fit - 1))
    offsets = sorted(rng.uniform(0, slack) for _ in range(fit))
    return [start + timedelta(seconds=offset + index * gap_seconds) for index, offset in enumerate(offsets)]


class DailyChatService:
    def __init__(
        self,
        accounts: Any,
        settings_file: Path = SETTINGS_FILE,
        plan_dir: Path = PLAN_DIR,
        now: Callable[[], datetime] = _utc_now,
        rng: random.Random | None = None,
    ):
        self.accounts = accounts
        self.settings_file = settings_file
        self.plan_dir = plan_dir
        self._now = now
        self._rng = rng or random.SystemRandom()
        self._lock = Lock()

    # ── settings ─────────────────────────────────────────────
    def get_settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update({key: value for key, value in (data or {}).items() if key in DEFAULT_SETTINGS})
        return merged

    def update_settings(self, incoming: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        for key, default in DEFAULT_SETTINGS.items():
            if key not in incoming:
                continue
            value = incoming[key]
            if isinstance(default, bool):
                current[key] = bool(value)
            elif isinstance(default, int):
                current[key] = max(0, int(value))
            else:
                current[key] = _parse_hhmm(value, str(current[key]))
        if current["window_end"] <= current["window_start"]:
            raise ValueError("结束时间必须晚于开始时间")
        current["min_gap_minutes"] = min(180, current["min_gap_minutes"])
        current["keep_days"] = max(1, min(365, current["keep_days"]))
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return current

    # ── plan files ───────────────────────────────────────────
    def today(self) -> date:
        return self._now().astimezone(CN_TZ).date()

    def _plan_path(self, day: date | str) -> Path:
        return self.plan_dir / f"{day}.json"

    def load_plan(self, day: date | str) -> dict[str, Any] | None:
        try:
            return json.loads(self._plan_path(day).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_plan(self, plan: dict[str, Any]) -> None:
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        path = self._plan_path(plan["date"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def list_days(self) -> list[dict[str, Any]]:
        if not self.plan_dir.exists():
            return []
        days = []
        for path in sorted(self.plan_dir.glob("*.json"), reverse=True):
            plan = self.load_plan(path.stem)
            if plan:
                days.append({"date": plan["date"], "counts": self.counts(plan)})
        return days

    def _cleanup(self, keep_days: int) -> None:
        cutoff = self.today() - timedelta(days=max(1, keep_days))
        for path in self.plan_dir.glob("*.json"):
            try:
                if date.fromisoformat(path.stem) < cutoff:
                    path.unlink(missing_ok=True)
            except ValueError:
                continue

    @staticmethod
    def counts(plan: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in plan.get("items", []):
            key = item["state"] if item["state"] != "done" else f"done:{item.get('result') or ''}"
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ── accounts ─────────────────────────────────────────────
    @staticmethod
    def account_key(account: dict[str, Any]) -> str:
        email = str(account.get("email") or "").strip().lower()
        user_id = str(account.get("user_id") or "").strip()
        return email or (f"user:{user_id}" if user_id else anonymize_token(account.get("access_token")))

    @staticmethod
    def ineligible_reason(account: dict[str, Any] | None, settings: dict[str, Any]) -> str:
        if not account:
            return "账号已不在号池"
        status = str(account.get("status") or "")
        if status == "禁用":
            return "账号已禁用"
        if status == "异常":
            return "账号异常"
        if status == "限流" and not settings.get("include_limited", True):
            return "设置为不给限流账号发送"
        if status not in ("正常", "限流"):
            return f"账号状态：{status or '未知'}"
        if account.get("relogin_paused"):
            return "已停止自动重登，需要手动处理"
        if not account.get("access_token"):
            return "没有 Access Token"
        return ""

    def _find_account(self, key: str) -> tuple[str, dict[str, Any] | None]:
        for account in self.accounts.list_accounts():
            if self.account_key(account) == key:
                return str(account.get("access_token") or ""), account
        return "", None

    # ── building a day ───────────────────────────────────────
    def _new_item(self, account: dict[str, Any]) -> dict[str, Any]:
        email = str(account.get("email") or "").strip()
        return {
            "id": uuid.uuid4().hex[:10],
            "key": self.account_key(account),
            "email": email,
            "label": email or anonymize_token(account.get("access_token")),
            "scheduled_at": None,
            "planned_at": None,
            "state": "pending",
        }

    @staticmethod
    def _skip(item: dict[str, Any], kind: str, reason: str) -> None:
        item.update({"state": "skipped", "skip_kind": kind, "reason": reason})

    def _assign_times(self, plan: dict[str, Any], items: list[dict[str, Any]], settings: dict[str, Any]) -> None:
        day = date.fromisoformat(plan["date"])
        start = max(_at(day, settings["window_start"]), self._now() + timedelta(minutes=1))
        end = _at(day, settings["window_end"])
        if not items:
            return
        if start >= end:
            for item in items:
                self._skip(item, "window", "今天的发送时间段已过")
            return
        self._rng.shuffle(items)
        times = spaced_times(start, end, len(items), settings["min_gap_minutes"] * 60, self._rng)
        for item, moment in zip(items, times):
            item["scheduled_at"] = _iso(moment)
            item["planned_at"] = item.get("planned_at") or item["scheduled_at"]
        for item in items[len(times):]:
            item["scheduled_at"] = None
            self._skip(item, "window", "今天剩下的时间按最短间隔排不下")

    def _fill_plan(self, plan: dict[str, Any], kept: list[dict[str, Any]], settings: dict[str, Any]) -> dict[str, Any]:
        done_keys = {item["key"] for item in kept}
        schedule, skipped = [], []
        for account in self.accounts.list_accounts():
            key = self.account_key(account)
            if key in done_keys:
                continue
            done_keys.add(key)
            item = self._new_item(account)
            # Only a disabled account is dropped up front: 异常 / paused ones are often fixed by
            # the morning patrol or by hand later, so they are checked again at send time.
            if account.get("status") == "禁用":
                self._skip(item, "ineligible", "账号已禁用")
                skipped.append(item)
            else:
                schedule.append(item)
        self._assign_times(plan, schedule, settings)
        plan["items"] = self._sorted([*kept, *schedule, *skipped])
        plan["window"] = f"{settings['window_start']}-{settings['window_end']}"
        plan["min_gap_minutes"] = settings["min_gap_minutes"]
        return plan

    @staticmethod
    def _sorted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def when(item: dict[str, Any]) -> str:
            if item["state"] in ("done", "running") and item.get("started_at"):
                return item["started_at"]
            return item.get("scheduled_at") or "9999"

        return sorted(items, key=lambda item: (when(item), item.get("label") or ""))

    def ensure_plan(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = settings or self.get_settings()
        with self._lock:
            today = self.today()
            plan = self.load_plan(today)
            if plan is not None:
                return plan
            plan = {"date": str(today), "created_at": _iso(self._now()), "items": []}
            self._fill_plan(plan, [], settings)
            self._save_plan(plan)
            self._cleanup(settings["keep_days"])
            return plan

    def reschedule_today(self) -> dict[str, Any]:
        """Re-pick times for everything not yet sent today, and add accounts that joined since."""

        settings = self.get_settings()
        self.ensure_plan(settings)
        with self._lock:
            plan = self.load_plan(self.today()) or {"date": str(self.today()), "items": []}
            kept = [
                item
                for item in plan.get("items", [])
                if item["state"] in ("done", "running") or item.get("skip_kind") == "interrupted"
            ]
            self._fill_plan(plan, kept, settings)
            plan["rescheduled_at"] = _iso(self._now())
            self._save_plan(plan)
            return plan

    # ── sending ──────────────────────────────────────────────
    def _update_item(self, day: str, item_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            plan = self.load_plan(day)
            if plan is None:
                return None
            for item in plan.get("items", []):
                if item["id"] == item_id:
                    item.update(changes)
                    self._save_plan(plan)
                    return dict(item)
        return None

    def _send(self, day: str, item: dict[str, Any], trigger: str) -> dict[str, Any] | None:
        """Send one item that is already marked running. Records the outcome on the plan."""

        started = self._now()
        try:
            settings = self.get_settings()
            token, account = self._find_account(item["key"])
            reason = self.ineligible_reason(account, settings)
            if reason:
                return self._update_item(
                    day, item["id"],
                    {"state": "skipped", "skip_kind": "ineligible", "reason": reason, "finished_at": _iso(self._now())},
                )
            result = self.accounts.test_real_chat(token)
            changes = {
                "state": "done",
                "result": result.get("status") or "测试失败",
                "error": result.get("error") or "",
                "prompt": result.get("prompt") or "",
                "prompt_id": result.get("prompt_id") or "",
                "conversation_url": result.get("conversation_url") or "",
            }
        except Exception as exc:
            changes = {"state": "done", "result": "测试失败", "error": str(exc)[:200]}
        finished = self._now()
        changes.update({
            "trigger": trigger,
            "finished_at": _iso(finished),
            "duration_seconds": round((finished - started).total_seconds(), 1),
        })
        return self._update_item(day, item["id"], changes)

    def _claim(self, plan: dict[str, Any], item: dict[str, Any], trigger: str) -> dict[str, Any]:
        item.update({"state": "running", "trigger": trigger, "started_at": _iso(self._now())})
        self._save_plan(plan)
        return dict(item)

    def take_due_item(self, settings: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """Claim the earliest due item. Missed items (server was down) get new times first."""

        with self._lock:
            day = str(self.today())
            plan = self.load_plan(day)
            if plan is None:
                return None
            now = self._now()
            pending = [item for item in plan.get("items", []) if item["state"] == "pending"]
            missed_before = now - timedelta(minutes=MISSED_AFTER_MINUTES)
            if any((_parse_iso(item["scheduled_at"]) or now) < missed_before for item in pending):
                for item in pending:
                    item["rescheduled"] = True
                self._assign_times(plan, pending, settings)
                plan["items"] = self._sorted(plan["items"])
                self._save_plan(plan)
            for item in plan["items"]:
                due = _parse_iso(item.get("scheduled_at"))
                if item["state"] == "pending" and due is not None and due <= now:
                    return day, self._claim(plan, item, "schedule")
        return None

    def run_now(self, item_id: str) -> dict[str, Any]:
        with self._lock:
            day = str(self.today())
            plan = self.load_plan(day)
            item = next((entry for entry in (plan or {}).get("items", []) if entry["id"] == item_id), None)
            if plan is None or item is None:
                raise KeyError("item not found")
            if item["state"] != "pending":
                raise ValueError("只有等待中的项目可以立即发送")
            claimed = self._claim(plan, item, "manual")
        Thread(target=self._send, args=(day, claimed, "manual"), name="daily-chat-manual", daemon=True).start()
        return claimed

    def close_stale(self, startup: bool = False) -> None:
        """Close what earlier days left open; at startup also today's sends cut off by the restart.

        On a normal tick only yesterday is checked; at startup every older plan is, in case the
        server was down for several days.
        """

        with self._lock:
            today = self.today()
            if startup:
                days = [today]
                for path in self.plan_dir.glob("*.json") if self.plan_dir.exists() else ():
                    try:
                        day = date.fromisoformat(path.stem)
                    except ValueError:
                        continue
                    if day < today:
                        days.append(day)
            else:
                days = [today - timedelta(days=1)]
            for day in days:
                plan = self.load_plan(day)
                if not plan:
                    continue
                changed = False
                for item in plan.get("items", []):
                    if item["state"] == "running":
                        self._skip(item, "interrupted", "服务重启，这次发送被中断")
                        changed = True
                    elif item["state"] == "pending" and day < today:
                        self._skip(item, "window", "当天没来得及发送")
                        changed = True
                if changed:
                    self._save_plan(plan)

    # ── scheduler ────────────────────────────────────────────
    def tick(self) -> None:
        settings = self.get_settings()
        self.close_stale()
        if not settings["enabled"]:
            return
        self.ensure_plan(settings)
        claimed = self.take_due_item(settings)
        if claimed:
            self._send(claimed[0], claimed[1], "schedule")

    def next_item_at(self, plan: dict[str, Any] | None) -> str | None:
        times = [item["scheduled_at"] for item in (plan or {}).get("items", []) if item["state"] == "pending" and item.get("scheduled_at")]
        return min(times) if times else None

    def start_scheduler(self, stop_event: Event) -> Thread:
        def worker() -> None:
            try:
                self.close_stale(startup=True)
            except Exception as exc:
                print(f"[daily-chat] startup cleanup failed: {exc}")
            while not stop_event.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    print(f"[daily-chat] tick failed: {exc}")
                stop_event.wait(TICK_SECONDS)

        thread = Thread(target=worker, name="daily-chat-scheduler", daemon=True)
        thread.start()
        return thread


def _default_service() -> DailyChatService:
    from services.account_service import account_service

    return DailyChatService(account_service)


daily_chat_service = _default_service()
