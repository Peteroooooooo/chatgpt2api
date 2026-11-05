from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from services.account_service import AccountService, account_service
from services.config import DATA_DIR
from utils.helper import anonymize_token

CN_TZ = timezone(timedelta(hours=8))
CREDENTIAL_LINE = re.compile(r"^([^\s:|]+@[^\s:|]+?)(?:----|[\t|:]|\s+)(.+)$")
SETTINGS_FILE = DATA_DIR / "keeper.json"
PATROL_DIR = DATA_DIR / "patrol"
DEFAULT_SETTINGS = {
    "patrol_enabled": True,
    "patrol_time": "04:30",
    "auto_relogin_enabled": True,
    "relogin_without_password": True,
    "relogin_cooldown_hours": 6,
    "push_auth_wait_seconds": 120,
    "relogin_delay_min_seconds": 20,
    "relogin_delay_max_seconds": 60,
    "relogin_when_access_token_expires_within_hours": 48,
    "keep_runs": 60,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    except Exception:
        pass
    return 4, 30


class AccountKeeper:
    """Daily patrol: refresh every account, re-login the ones that dropped, log the outcome."""

    def __init__(self, accounts: AccountService, settings_file: Path = SETTINGS_FILE, patrol_dir: Path = PATROL_DIR):
        self.accounts = accounts
        self.settings_file = settings_file
        self.patrol_dir = patrol_dir
        self._lock = Lock()
        self._running: dict[str, Any] | None = None
        self._jobs: dict[str, dict[str, Any]] = {}
        self._sleep = Event().wait
        self._apply_policy(self.get_settings())

    def _apply_policy(self, settings: dict[str, Any]) -> None:
        apply = getattr(self.accounts, "apply_relogin_policy", None)
        if apply:
            apply(settings)

    # ── settings ─────────────────────────────────────────────
    def get_settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in (data or {}).items() if k in DEFAULT_SETTINGS})
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
                hour, minute = _parse_hhmm(str(value))
                current[key] = f"{hour:02d}:{minute:02d}"
        if current["relogin_delay_max_seconds"] < current["relogin_delay_min_seconds"]:
            current["relogin_delay_max_seconds"] = current["relogin_delay_min_seconds"]
        current["push_auth_wait_seconds"] = min(600, current["push_auth_wait_seconds"])
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._apply_policy(current)
        return current

    def next_run_at(self, now: datetime | None = None) -> datetime:
        now = (now or _now()).astimezone(CN_TZ)
        hour, minute = _parse_hhmm(self.get_settings()["patrol_time"])
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.astimezone(timezone.utc)

    # ── one account ──────────────────────────────────────────
    def _needs_relogin_without_rt(self, account: dict, hours: int) -> bool:
        remaining = AccountService._token_expires_in(str(account.get("access_token") or ""))
        return remaining is not None and remaining <= hours * 3600

    def _refresh(self, token: str, account: dict) -> tuple[str, str]:
        """Force a session_token / refresh_token renewal. Returns (new_token, error)."""
        return self.accounts.force_renew(token, "patrol")

    def _relogin(self, token: str, account: dict) -> tuple[str, str]:
        email = str(account.get("email") or "").strip()
        before = self.accounts.get_account(token) or {}
        self.accounts._password_re_login_thread(token, email, str(account.get("password") or ""), "patrol")
        new_token = self.accounts.find_token_by_email(email) or self.accounts.resolve_access_token(token)
        after = self.accounts.get_account(new_token) or {}
        if after.get("last_relogin_error"):
            return new_token, str(after["last_relogin_error"])
        if after.get("status") != "正常" or after.get("access_token") == before.get("access_token"):
            return new_token, "relogin finished without new token"
        return new_token, ""

    def check_account(self, token: str, settings: dict) -> dict[str, Any]:
        account = self.accounts.get_account(token) or {}
        email = str(account.get("email") or "").strip()
        has_password = bool(str(account.get("password") or "").strip())
        has_rt = bool(str(account.get("refresh_token") or "").strip() or str(account.get("session_token") or "").strip())
        entry: dict[str, Any] = {
            "at": _now().isoformat(),
            "email": email,
            "token": anonymize_token(token),
            "status_before": account.get("status"),
            "has_refresh_token": has_rt,
            "has_password": has_password,
            "actions": [],
            "result": "ok",
            "error": "",
        }
        if account.get("status") == "禁用":
            entry["result"] = "skipped"
            entry["error"] = "account disabled"
            return entry

        need_relogin = False
        if has_rt:
            entry["actions"].append("refresh")
            token, error = self._refresh(token, account)
            if error:
                entry["refresh_error"] = error[:300]
                need_relogin = True
            else:
                entry["actions"].append("verify")
                self.accounts.refresh_accounts([token], defer_invalid_removal=True)
                token = self.accounts.resolve_access_token(token)
                if (self.accounts.get_account(token) or {}).get("status") == "异常":
                    entry["refresh_error"] = "renewed token still rejected by ChatGPT"
                    need_relogin = True
        else:
            need_relogin = account.get("status") == "异常" or self._needs_relogin_without_rt(
                account, int(settings["relogin_when_access_token_expires_within_hours"])
            )

        if need_relogin:
            creds = self.accounts.relogin_credentials(account) if email else None
            if not creds:
                entry["result"] = "needs_manual"
                reason = "no password and email-code login is off" if not self.accounts.relogin_without_password else "no password and no mailbox route"
                entry["error"] = entry.get("refresh_error") or f"{reason}, cannot re-login"
                return entry
            entry["actions"].append("relogin" if creds[1] else "relogin_email_code")
            token, error = self._relogin(token, self.accounts.get_account(token) or account)
            entry["result"] = "relogin_failed" if error else "relogged_in"
            entry["error"] = error[:300]
        entry["status_after"] = (self.accounts.get_account(token) or {}).get("status")
        return entry

    # ── patrol run ───────────────────────────────────────────
    def _runs_index(self) -> Path:
        return self.patrol_dir / "runs.json"

    def list_runs(self) -> list[dict[str, Any]]:
        try:
            runs = json.loads(self._runs_index().read_text(encoding="utf-8"))
        except Exception:
            runs = []
        if self._running:
            runs = [dict(self._running), *runs]
        return runs

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        summary = next((run for run in self.list_runs() if run.get("id") == run_id), None)
        if summary is None:
            return None
        path = self.patrol_dir / f"{run_id}.jsonl"
        entries = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
        return {**summary, "entries": entries}

    def _save_run(self, summary: dict[str, Any]) -> None:
        keep = int(self.get_settings()["keep_runs"]) or 60
        runs = [run for run in self.list_runs() if run.get("id") != summary["id"] and run.get("state") != "running"]
        runs = [summary, *runs]
        for stale in runs[keep:]:
            (self.patrol_dir / f"{stale.get('id')}.jsonl").unlink(missing_ok=True)
        self.patrol_dir.mkdir(parents=True, exist_ok=True)
        self._runs_index().write_text(json.dumps(runs[:keep], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run_patrol(self, trigger: str = "manual") -> dict[str, Any]:
        with self._lock:
            if self._running:
                return dict(self._running)
            run_id = _now().astimezone(CN_TZ).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
            summary: dict[str, Any] = {
                "id": run_id,
                "trigger": trigger,
                "state": "running",
                "started_at": _now().isoformat(),
                "finished_at": None,
                "total": 0,
                "done": 0,
                "counts": {},
            }
            self._running = summary
        settings = self.get_settings()
        self.patrol_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.patrol_dir / f"{run_id}.jsonl"
        try:
            tokens = self.accounts.list_tokens()
            summary["total"] = len(tokens)
            for token in tokens:
                try:
                    entry = self.check_account(token, settings)
                except Exception as exc:
                    entry = {"at": _now().isoformat(), "token": anonymize_token(token), "result": "error", "error": str(exc)[:300]}
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                counts = summary["counts"]
                counts[entry["result"]] = int(counts.get(entry["result"], 0)) + 1
                summary["done"] += 1
                if "relogin" in entry.get("actions", []):
                    low = int(settings["relogin_delay_min_seconds"])
                    high = int(settings["relogin_delay_max_seconds"])
                    self._sleep(random.uniform(low, max(low, high)))
            summary["state"] = "finished"
        except Exception as exc:
            summary["state"] = "failed"
            summary["error"] = str(exc)[:300]
        finally:
            summary["finished_at"] = _now().isoformat()
            with self._lock:
                self._running = None
                self._save_run(summary)
        return summary

    def start_patrol(self, trigger: str = "manual") -> dict[str, Any]:
        if self._running:
            return dict(self._running)
        Thread(target=self.run_patrol, args=(trigger,), name="account-patrol", daemon=True).start()
        for _ in range(20):
            if self._running:
                return dict(self._running)
            Event().wait(0.05)
        return {"state": "starting"}

    def start_scheduler(self, stop_event: Event) -> Thread:
        def worker() -> None:
            while not stop_event.is_set():
                due = self.next_run_at()
                while not stop_event.is_set() and _now() < due:
                    stop_event.wait(min(60.0, max(1.0, (due - _now()).total_seconds())))
                if stop_event.is_set():
                    return
                if self.get_settings()["patrol_enabled"]:
                    try:
                        self.run_patrol("schedule")
                    except Exception as exc:
                        print(f"[account-patrol] failed: {exc}")
                stop_event.wait(61)

        thread = Thread(target=worker, name="account-patrol-scheduler", daemon=True)
        thread.start()
        return thread

    # ── email + password import ─────────────────────────────
    @staticmethod
    def parse_credentials(text: str) -> list[tuple[str, str]]:
        """One account per line: email----password, email:password, email|password or tab/space separated."""
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw in str(text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = CREDENTIAL_LINE.match(line)
            if not match:
                continue
            email, password = match.group(1).strip(), match.group(2).strip()
            if not password or email.lower() in seen:
                continue
            seen.add(email.lower())
            result.append((email, password))
        return result

    def start_import(self, text: str) -> dict[str, Any]:
        credentials = self.parse_credentials(text)
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "state": "running", "total": len(credentials), "done": 0, "items": []}
        self._jobs[job_id] = job
        settings = self.get_settings()

        def worker() -> None:
            for index, (email, password) in enumerate(credentials):
                try:
                    result = self.accounts.login_and_add_account(email, password)
                except Exception as exc:
                    result = {"ok": False, "email": email, "error": str(exc)}
                if "detail" in result:
                    result["detail"] = str(result["detail"])[:300]
                job["items"].append(result)
                job["done"] += 1
                if index < len(credentials) - 1:
                    low = int(settings["relogin_delay_min_seconds"])
                    self._sleep(random.uniform(low, max(low, int(settings["relogin_delay_max_seconds"]))))
            job["state"] = "finished"

        if credentials:
            Thread(target=worker, name="password-import", daemon=True).start()
        else:
            job["state"] = "finished"
        return dict(job)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        return dict(job) if job else None


account_keeper = AccountKeeper(account_service)
