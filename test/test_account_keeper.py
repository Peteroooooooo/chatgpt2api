import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from services.account_keeper import AccountKeeper
from services.account_service import AccountService
from test.test_account_export import MemoryStorage, make_jwt


def token(name: str, expires_in: int = 9 * 86400) -> str:
    return make_jwt({"exp": int(time.time()) + expires_in, "iat": int(time.time()), "n": name})


class PatrolService(AccountService):
    """AccountService with OpenAI calls replaced."""

    def __init__(self, accounts, refresh_ok=True, login_ok=True, routed_domain=None, login_error="otp_not_received"):
        super().__init__(MemoryStorage(accounts))
        self.refresh_ok = refresh_ok
        self.login_ok = login_ok
        self.login_error = login_error
        self.routed_domain = routed_domain
        self.logins: list[str] = []
        self.push_waits: list[int] = []

    def relogin_credentials(self, account):
        email = str(account.get("email") or "")
        password = str(account.get("password") or "")
        if email and password:
            return email, password
        if email and self.relogin_without_password and self.routed_domain and email.endswith("@" + self.routed_domain):
            return email, ""
        return None

    def _request_access_token_refresh(self, refresh_token, account=None):
        if not self.refresh_ok:
            raise RuntimeError("oauth_refresh_http_401: app_session_terminated")
        return {"access_token": token("refreshed-" + refresh_token), "refresh_token": refresh_token + "+", "id_token": ""}

    def _login_with_password(self, email, password, push_wait_seconds=0):
        self.logins.append(email)
        self.push_waits.append(push_wait_seconds)
        if not self.login_ok:
            return {"ok": False, "error": self.login_error}
        return {"ok": True, "email": email, "access_token": token("login-" + email), "refresh_token": "rt-new", "id_token": ""}

    def refresh_accounts(self, access_tokens, progress_id=None, defer_invalid_removal=True):
        return {"refreshed": 0, "errors": [], "items": self.list_accounts(), "relogined": 0}


class KeeperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.settings_file = root / "keeper.json"
        self.patrol_dir = root / "patrol"

    def tearDown(self):
        self.tmp.cleanup()

    def keeper(self, service):
        keeper = AccountKeeper(service, self.settings_file, self.patrol_dir)
        keeper._sleep = lambda _: None
        return keeper

    def test_refresh_ok_and_failed_refresh_relogs_in(self):
        good = {"access_token": token("a"), "refresh_token": "rt-a", "email": "a@040601.com", "password": "p"}
        service = PatrolService([good])
        summary = self.keeper(service).run_patrol("test")
        self.assertEqual(summary["counts"], {"ok": 1})
        self.assertEqual(service.logins, [])

        service = PatrolService([dict(good)], refresh_ok=False)
        keeper = self.keeper(service)
        summary = keeper.run_patrol("test")
        self.assertEqual(summary["counts"], {"relogged_in": 1})
        account = service.get_account(service.find_token_by_email("a@040601.com"))
        self.assertEqual(account["refresh_token"], "rt-new")
        self.assertEqual(account["status"], "正常")
        run = keeper.get_run(summary["id"])
        self.assertEqual(run["entries"][0]["actions"], ["refresh", "relogin"])

    def test_failed_relogin_keeps_account_and_password(self):
        service = PatrolService(
            [{"access_token": token("b"), "refresh_token": "rt-b", "email": "b@peter.com", "password": "p", "status": "异常"}],
            login_ok=False,
        )
        summary = self.keeper(service).run_patrol("test")
        self.assertEqual(summary["counts"], {"relogin_failed": 1})
        account = service.get_account(service.list_tokens()[0])
        self.assertEqual(account["password"], "p")
        self.assertEqual(account["status"], "异常")
        self.assertEqual(account["relogin_fail_count"], 1)
        self.assertEqual(account["last_relogin_error"], "otp_not_received")

    def test_auto_relogin_cooldown_but_patrol_forces(self):
        service = PatrolService([{"access_token": token("c"), "email": "c@duck.com", "password": "p"}], login_ok=False)
        tok = service.list_tokens()[0]
        service._password_re_login_thread(tok, "c@duck.com", "p", "refresh_token_keepalive")
        service._password_re_login_thread(tok, "c@duck.com", "p", "refresh_token_keepalive")
        self.assertEqual(len(service.logins), 1)
        service._password_re_login_thread(tok, "c@duck.com", "p", "patrol")
        self.assertEqual(len(service.logins), 2)

    def test_error_that_needs_the_user_stops_at_once(self):
        service = PatrolService(
            [{"access_token": token("h"), "email": "h@gmail.com", "password": "p", "status": "异常"}],
            login_ok=False,
            login_error="google_sign_in_account",
        )
        keeper = self.keeper(service)
        self.assertEqual(keeper.run_patrol("test")["counts"], {"relogin_failed": 1})
        tok = service.list_tokens()[0]
        self.assertTrue(service.get_account(tok)["relogin_paused"])
        summary = keeper.run_patrol("test")
        self.assertEqual(summary["counts"], {"needs_manual": 1})
        self.assertIn("google_sign_in_account", keeper.get_run(summary["id"])["entries"][0]["error"])
        service._password_re_login_thread(tok, "h@gmail.com", "p", "refresh_token_keepalive")
        self.assertEqual(len(service.logins), 1)

        service.login_ok = True
        service._password_re_login_thread(tok, "h@gmail.com", "p", "manual_relogin")
        account = service.get_account(service.find_token_by_email("h@gmail.com"))
        self.assertEqual((account["status"], account["relogin_paused"], account["relogin_fail_count"]), ("正常", False, 0))

    def test_temporary_errors_stop_after_three_in_a_row(self):
        service = PatrolService(
            [{"access_token": token("i"), "email": "i@duck.com", "password": "p", "status": "异常"}], login_ok=False
        )
        keeper = self.keeper(service)
        results = [next(iter(keeper.run_patrol("test")["counts"])) for _ in range(4)]
        self.assertEqual(results, ["relogin_failed"] * 3 + ["needs_manual"])
        self.assertEqual(len(service.logins), 3)

    def test_zero_max_failures_keeps_retrying_temporary_errors(self):
        service = PatrolService(
            [{"access_token": token("j"), "email": "j@duck.com", "password": "p", "status": "异常"}], login_ok=False
        )
        keeper = self.keeper(service)
        keeper.update_settings({"relogin_max_failures": 0})
        for _ in range(4):
            keeper.run_patrol("test")
        self.assertEqual(len(service.logins), 4)

    def test_only_manual_relogin_waits_for_phone_approval(self):
        service = PatrolService([{"access_token": token("g"), "email": "g@duck.com", "password": "p"}], login_ok=False)
        tok = service.list_tokens()[0]
        for event in ("patrol", "manual_relogin", "refresh_token_keepalive"):
            service.update_account(tok, {"relogin_fail_count": 0}, quiet=True)
            service._password_re_login_thread(tok, "g@duck.com", "p", event)
        self.assertEqual(service.push_waits, [0, 120, 0])

    def test_access_token_only_account(self):
        service = PatrolService([
            {"access_token": token("d", expires_in=3600), "email": "d@x.com", "password": "p"},
            {"access_token": token("e", expires_in=3600), "email": "e@x.com"},
            {"access_token": token("f"), "email": "f@x.com", "password": "p"},
        ])
        summary = self.keeper(service).run_patrol("test")
        self.assertEqual(summary["counts"], {"relogged_in": 1, "needs_manual": 1, "ok": 1})
        self.assertEqual(service.logins, ["d@x.com"])

    def test_no_password_relogs_in_with_email_code_when_mailbox_routes(self):
        service = PatrolService([{"access_token": token("g"), "session_token": "st", "email": "g@duck.com", "status": "异常"}], routed_domain="duck.com")
        service.force_renew = lambda tok, event="patrol": (tok, "session_token: 401")
        keeper = self.keeper(service)
        summary = keeper.run_patrol("test")
        self.assertEqual(summary["counts"], {"relogged_in": 1})
        self.assertEqual(service.logins, ["g@duck.com"])
        self.assertEqual(keeper.get_run(summary["id"])["entries"][0]["actions"], ["refresh", "relogin_email_code"])

        service = PatrolService([{"access_token": token("h"), "session_token": "st", "email": "h@other.com", "status": "异常"}], routed_domain="duck.com")
        service.force_renew = lambda tok, event="patrol": (tok, "session_token: 401")
        keeper = self.keeper(service)
        self.assertEqual(keeper.run_patrol("test")["counts"], {"needs_manual": 1})
        self.assertEqual(service.logins, [])

    def test_settings_switch_off_email_code_and_auto_relogin(self):
        service = PatrolService([{"access_token": token("i"), "session_token": "st", "email": "i@duck.com", "status": "异常"}], routed_domain="duck.com")
        service.force_renew = lambda tok, event="patrol": (tok, "session_token: 401")
        keeper = self.keeper(service)
        keeper.update_settings({"relogin_without_password": False, "auto_relogin_enabled": False, "relogin_cooldown_hours": 2})
        self.assertFalse(service.relogin_without_password)
        self.assertEqual(service.relogin_cooldown_seconds, 7200)
        self.assertEqual(keeper.run_patrol("test")["counts"], {"needs_manual": 1})
        tok = service.list_tokens()[0]
        self.assertFalse(service._relogin_allowed(tok, "refresh_token_keepalive"))
        self.assertTrue(service._relogin_allowed(tok, "patrol"))

        fresh = self.keeper(PatrolService([]))
        self.assertFalse(fresh.accounts.auto_relogin_enabled)

    def test_parse_credentials(self):
        text = "a@x.com----p:w1\nb@y.com:pw2\n# skip\nbad line\nc@z.com\tpw 3\na@x.com----dup"
        self.assertEqual(
            AccountKeeper.parse_credentials(text),
            [("a@x.com", "p:w1"), ("b@y.com", "pw2"), ("c@z.com", "pw 3")],
        )

    def test_login_and_add_new_account(self):
        service = PatrolService([])
        result = service.login_and_add_account("new@040601.com", "pw")
        self.assertTrue(result["ok"])
        account = service.get_account(service.find_token_by_email("new@040601.com"))
        self.assertEqual(account["password"], "pw")
        self.assertEqual(account["refresh_token"], "rt-new")

    def test_runs_are_trimmed(self):
        service = PatrolService([])
        keeper = self.keeper(service)
        keeper.update_settings({"keep_runs": 2})
        ids = []
        for _ in range(3):
            ids.append(keeper.run_patrol("test")["id"])
            time.sleep(1.01)
        self.assertEqual([run["id"] for run in keeper.list_runs()], ids[:0:-1])


if __name__ == "__main__":
    unittest.main()


class TokenExpiryTests(unittest.TestCase):
    def test_stored_expiry_follows_the_token(self):
        fresh = token("k", expires_in=10 * 86400)
        service = AccountService(MemoryStorage([{"access_token": fresh, "email": "k@x.com", "access_token_expires_at": "2026-09-22T18:35:43+00:00"}]))
        stored = service.get_account(fresh)["access_token_expires_at"]
        self.assertEqual(stored, datetime.fromtimestamp(AccountService._jwt_exp(fresh), timezone.utc).isoformat())
        service.update_account(fresh, {"access_token_expires_at": "2020-01-01T00:00:00+00:00"}, quiet=True)
        self.assertEqual(service.get_account(fresh)["access_token_expires_at"], stored)
