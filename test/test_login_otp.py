import unittest
from datetime import datetime, timezone

from services.login_otp import (
    PUSH_DEVICES_URL,
    PUSH_STATUS_URL,
    PUSH_VALIDATE_URL,
    SEND_OTP_URL,
    VALIDATE_OTP_URL,
    complete_email_otp,
    complete_push_auth,
    follow_to_auth_code,
    push_request_id,
)
from services.mailbox.base import MailboxError

START = datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc)
CALLBACK = "https://platform.openai.com/auth/callback?code=abc123&state=xyz"
CONSENT = "https://auth.openai.com/api/oauth/oauth2/auth?x=1"


class Response:
    def __init__(self, status=200, json_data=None, location=""):
        self.status_code = status
        self._json = json_data
        self.text = "{}" if json_data is not None else ""
        self.headers = {"Location": location} if location else {}

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, validate_responses, redirects=None):
        self.validate_responses = list(validate_responses)
        self.redirects = dict(redirects or {})
        self.gets = []
        self.posts = []

    def get(self, url, **_):
        self.gets.append(url)
        if url in self.redirects:
            return Response(302, location=self.redirects[url])
        return Response(200)

    def post(self, url, json=None, **_):
        self.posts.append((url, json))
        return self.validate_responses.pop(0)


class FakeMailbox:
    def __init__(self, codes, routed=True):
        self.codes = list(codes)
        self.routed = routed
        self.excludes = []

    def resolve(self, email):
        return object() if self.routed else None

    def wait_for_code(self, email, since, exclude=None):
        self.excludes.append(set(exclude or set()))
        code = self.codes.pop(0)
        if isinstance(code, Exception):
            raise code
        return code


LOGIN_DATA = {"continue_url": "https://auth.openai.com/email-verification", "page": {"type": "email_otp_verification"}}


class LoginOtpTests(unittest.TestCase):
    def test_success_follows_redirects_to_callback(self):
        session = FakeSession([Response(200, {"continue_url": CONSENT})], {CONSENT: CALLBACK})
        result = complete_email_otp(session, "a@040601.com", LOGIN_DATA, {}, START, FakeMailbox(["123456"]))
        self.assertEqual(result, {"ok": True, "code": "abc123"})
        self.assertEqual(session.gets[0], "https://auth.openai.com/email-verification")
        self.assertEqual(session.posts, [(VALIDATE_OTP_URL, {"code": "123456"})])

    def test_rejected_code_ends_the_import(self):
        session = FakeSession([Response(401, {"error": "bad"}), Response(200, {"continue_url": CALLBACK})])
        mailbox = FakeMailbox(["111111", "222222"])
        result = complete_email_otp(session, "a@x.com", LOGIN_DATA, {}, START, mailbox)
        self.assertEqual(result["error"], "otp_validate_failed_401")
        self.assertEqual(len(mailbox.excludes), 1)
        self.assertEqual(session.posts, [(VALIDATE_OTP_URL, {"code": "111111"})])

    def test_timeout_ends_without_resend(self):
        session = FakeSession([])
        mailbox = FakeMailbox([MailboxError("none")])
        result = complete_email_otp(session, "a@x.com", LOGIN_DATA, {}, START, mailbox)
        self.assertEqual(result["error"], "otp_not_received")
        self.assertNotIn("https://auth.openai.com/api/accounts/email-otp/send", session.gets)
        self.assertEqual(len(mailbox.excludes), 1)

    def test_no_route_keeps_original_error(self):
        result = complete_email_otp(FakeSession([]), "a@x.com", LOGIN_DATA, {}, START, FakeMailbox([], routed=False))
        self.assertEqual(result["error"], "need_verification_code")

    def test_chain_ending_on_page_returns_no_code(self):
        session = FakeSession([], {CONSENT: "/workspace"})
        code, last = follow_to_auth_code(session, CONSENT, {})
        self.assertEqual(code, "")
        self.assertEqual(last, "https://auth.openai.com/workspace")


if __name__ == "__main__":
    unittest.main()


PUSH_DATA = {
    "continue_url": "https://auth.openai.com/push-auth-verification/req123",
    "page": {
        "type": "push_auth_verification",
        "payload": {"mfa_request_id": "req123", "challenge_mode": "approve_deny", "match_number": None, "can_use_email_fallback": True},
    },
}


class PushSession:
    """Answers by URL; status_answers are consumed one poll at a time."""

    def __init__(self, status_answers, validate=None, email_send=None, email_validate=None, redirects=None):
        self.status_answers = list(status_answers)
        self.validate = validate
        self.email_send = email_send
        self.email_validate = email_validate
        self.redirects = dict(redirects or {})
        self.posts = []
        self.headers = []

    def get(self, url, **_):
        if url == PUSH_DEVICES_URL:
            return Response(200, {"devices": [{"device_name": "Android 16 PLG110"}]})
        if url in self.redirects:
            return Response(302, location=self.redirects[url])
        return Response(200)

    def post(self, url, json=None, headers=None, **_):
        self.posts.append(url)
        self.headers.append(headers or {})
        if url == PUSH_STATUS_URL:
            return self.status_answers.pop(0)
        if url == PUSH_VALIDATE_URL:
            return self.validate
        if url == SEND_OTP_URL:
            return self.email_send
        if url == VALIDATE_OTP_URL:
            return self.email_validate
        raise AssertionError(url)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run_push(session, mailbox=None, wait=120, data=PUSH_DATA):
    clock = Clock()
    result = complete_push_auth(
        session, "a@040601.xyz", data, {}, mailbox or FakeMailbox(["123456"]), wait,
        lambda: {"openai-sentinel-token": "s"}, sleep=clock.sleep, clock=clock,
    )
    return result, clock


class PushAuthTests(unittest.TestCase):
    def test_request_id_from_payload_or_url(self):
        self.assertEqual(push_request_id(PUSH_DATA), "req123")
        self.assertEqual(push_request_id({"continue_url": "https://auth.openai.com/push-auth-verification/abc?x=1"}), "abc")
        self.assertEqual(push_request_id({"continue_url": "https://auth.openai.com/push-auth-verification/wait"}), "")

    def test_approved_prompt_finishes_the_login(self):
        session = PushSession(
            [Response(200, {"status": "pending"}), Response(200, {"status": "approved"})],
            validate=Response(200, {"continue_url": CONSENT}),
            redirects={CONSENT: CALLBACK},
        )
        result, clock = run_push(session)
        self.assertEqual(result, {"ok": True, "code": "abc123", "via": "push"})
        self.assertEqual(clock.now, 5)
        self.assertEqual(session.headers[0].get("openai-sentinel-token"), "s")

    def test_denied_prompt_stops_without_email_fallback(self):
        session = PushSession([Response(200, {"status": "denied"})])
        result, _ = run_push(session)
        self.assertEqual(result["error"], "push_auth_denied")
        self.assertNotIn(SEND_OTP_URL, session.posts)

    def test_unanswered_prompt_waits_then_uses_email_code(self):
        pending = [Response(200, {"status": "pending"}) for _ in range(30)]
        session = PushSession(
            pending,
            email_send=Response(200, {"continue_url": "https://auth.openai.com/email-verification", "page": {"type": "email_otp_verification"}}),
            email_validate=Response(200, {"continue_url": CALLBACK}),
        )
        result, clock = run_push(session, wait=120)
        self.assertEqual(result, {"ok": True, "code": "abc123", "via": "email_fallback"})
        self.assertEqual(clock.now, 120)
        self.assertEqual(session.posts.count(PUSH_STATUS_URL), 25)

    def test_zero_wait_goes_straight_to_email_code(self):
        session = PushSession(
            [],
            email_send=Response(200, {"page": {"type": "email_otp_verification"}}),
            email_validate=Response(200, {"continue_url": CALLBACK}),
        )
        result, _ = run_push(session, wait=0)
        self.assertTrue(result["ok"])
        self.assertNotIn(PUSH_STATUS_URL, session.posts)

    def test_expired_prompt_without_mailbox_route_reports_timeout(self):
        session = PushSession([Response(400, {"error": {"code": "login_request_expired"}})])
        result, _ = run_push(session, mailbox=FakeMailbox([], routed=False))
        self.assertEqual(result["error"], "push_auth_timeout")
        self.assertEqual(result["detail"]["push_outcome"], "expired")
        self.assertEqual(result["detail"]["devices"], ["Android 16 PLG110"])
