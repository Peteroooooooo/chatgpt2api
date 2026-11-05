import unittest
from unittest import mock

from services.signup import register_account

AUTHORIZE = "https://auth.openai.com/oauth/authorize?client_id=app_X"
CALLBACK = "https://chatgpt.com/api/auth/callback/openai?code=ac.1&state=s"


class Response:
    def __init__(self, status=200, json_data=None, url="", location=""):
        self.status_code = status
        self._json = json_data
        self.text = "{}" if json_data is not None else ""
        self.url = url
        self.headers = {"Location": location} if location else {}

    def json(self):
        return self._json


class Cookies(dict):
    def set(self, name, value, domain=None):
        self[name] = value


class FakeSession:
    def __init__(self, landing, posts):
        self.landing = landing
        self.post_responses = dict(posts)
        self.cookies = Cookies({"oai-did": "did-1"})
        self.gets = []
        self.posts = []

    def get(self, url, **_):
        self.gets.append(url)
        if url.endswith("/api/auth/csrf"):
            return Response(200, {"csrfToken": "csrf"})
        if url == AUTHORIZE:
            return Response(200, url=self.landing)
        if url == "https://auth.openai.com/about-you":
            return Response(200, url=url)
        if url.startswith("https://auth.openai.com/after-otp"):
            return Response(302, location="/about-you")
        return Response(200, url=url)

    def post(self, url, json=None, **_):
        self.posts.append((url, json))
        for prefix, response in self.post_responses.items():
            if url.startswith(prefix):
                return response
        raise AssertionError(f"unexpected POST {url}")


class FakeMailbox:
    def __init__(self, code="123456"):
        self.code = code
        self.calls = 0

    def wait_for_code(self, email, since, exclude=None):
        self.calls += 1
        return self.code


def sentinel(device_id, flow):
    return f"token-{flow}", ""


SIGNIN = {"https://chatgpt.com/api/auth/signin/openai": Response(200, {"url": AUTHORIZE})}


class SignupTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch(
            "services.signup.complete_about_you",
            return_value={"ok": True, "continue_url": CALLBACK},
        )
        self.profile = patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_email_with_password_registers_and_fills_profile(self):
        session = FakeSession(
            "https://auth.openai.com/create-account/password",
            {
                **SIGNIN,
                "https://auth.openai.com/api/accounts/user/register": Response(200, {"continue_url": "https://auth.openai.com/api/accounts/email-otp/send"}),
                "https://auth.openai.com/api/accounts/email-otp/validate": Response(200, {"continue_url": "https://auth.openai.com/after-otp", "page": {"type": "about_you"}}),
            },
        )
        result = register_account(session, "new@x.com", "pw ", FakeMailbox(), sentinel=sentinel)
        self.assertEqual(result, {"ok": True, "created": True, "password_set": True})
        register = [body for url, body in session.posts if url.endswith("/user/register")]
        self.assertEqual(register, [{"password": "pw ", "username": "new@x.com"}])
        self.profile.assert_called_once()

    def test_new_email_without_password_asks_for_the_code_then_uses_it(self):
        session = FakeSession(
            "https://auth.openai.com/email-verification",
            {
                **SIGNIN,
                "https://auth.openai.com/api/accounts/passwordless/send-otp": Response(200, {"continue_url": "https://auth.openai.com/email-verification"}),
                "https://auth.openai.com/api/accounts/email-otp/validate": Response(200, {"continue_url": "https://auth.openai.com/after-otp", "page": {"type": "about_you"}}),
            },
        )
        mailbox = FakeMailbox()
        result = register_account(session, "new@x.com", "", mailbox, sentinel=sentinel)
        self.assertEqual(result, {"ok": True, "created": True, "password_set": False})
        self.assertEqual(mailbox.calls, 1)
        urls = [url for url, _ in session.posts]
        self.assertLess(urls.index("https://auth.openai.com/api/accounts/passwordless/send-otp"), urls.index("https://auth.openai.com/api/accounts/email-otp/validate"))
        self.assertFalse(any(url.endswith("/user/register") for url in urls))

    def test_stale_code_is_skipped_for_the_next_mail(self):
        codes = iter(["111111", "222222"])
        wrong = Response(401, {"error": {"code": "wrong_email_otp_code"}})
        good = Response(200, {"continue_url": "https://auth.openai.com/after-otp", "page": {"type": "about_you"}})
        session = FakeSession(
            "https://auth.openai.com/email-verification",
            {**SIGNIN, "https://auth.openai.com/api/accounts/passwordless/send-otp": Response(200, {})},
        )
        original_post = session.post

        def post(url, json=None, **kw):
            if url.endswith("/email-otp/validate"):
                session.posts.append((url, json))
                return good if json["code"] == "222222" else wrong
            return original_post(url, json=json, **kw)

        session.post = post
        mailbox = FakeMailbox()
        mailbox.wait_for_code = lambda email, since, exclude=None: next(codes)
        result = register_account(session, "new@x.com", "", mailbox, sentinel=sentinel)
        self.assertTrue(result["ok"])
        sent = [body["code"] for url, body in session.posts if url.endswith("/email-otp/validate")]
        self.assertEqual(sent, ["111111", "222222"])

    def test_password_page_without_password_asks_for_one(self):
        session = FakeSession("https://auth.openai.com/create-account/password", SIGNIN)
        result = register_account(session, "new@x.com", "", FakeMailbox(), sentinel=sentinel)
        self.assertEqual(result["error"], "signup_password_required")

    def test_existing_account_is_left_alone(self):
        session = FakeSession("https://auth.openai.com/log-in/password", SIGNIN)
        mailbox = FakeMailbox()
        result = register_account(session, "old@x.com", "pw", mailbox, sentinel=sentinel)
        self.assertEqual(result["ok"], True)
        self.assertFalse(result["created"])
        self.assertEqual(mailbox.calls, 0)

    def test_rate_limit_is_reported(self):
        session = FakeSession(
            "https://auth.openai.com/create-account/password",
            {**SIGNIN, "https://auth.openai.com/api/accounts/user/register": Response(429, {"error": {"code": "rate_limit_exceeded"}})},
        )
        result = register_account(session, "new@x.com", "pw", FakeMailbox(), sentinel=sentinel)
        self.assertEqual(result["error"], "rate_limit_exceeded")


if __name__ == "__main__":
    unittest.main()
