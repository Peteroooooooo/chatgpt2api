import unittest
from datetime import datetime, timezone

from services.login_otp import VALIDATE_OTP_URL, complete_email_otp, follow_to_auth_code
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

    def test_rejected_code_is_excluded_on_retry(self):
        session = FakeSession([Response(401, {"error": "bad"}), Response(200, {"continue_url": CALLBACK})])
        mailbox = FakeMailbox(["111111", "222222"])
        result = complete_email_otp(session, "a@x.com", LOGIN_DATA, {}, START, mailbox)
        self.assertTrue(result["ok"])
        self.assertEqual(mailbox.excludes[1], {"111111"})

    def test_timeout_triggers_resend_then_fails(self):
        session = FakeSession([])
        mailbox = FakeMailbox([MailboxError("none"), MailboxError("still none")])
        result = complete_email_otp(session, "a@x.com", LOGIN_DATA, {}, START, mailbox)
        self.assertEqual(result["error"], "otp_not_received")
        self.assertIn("https://auth.openai.com/api/accounts/email-otp/send", session.gets)

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
