import unittest
from datetime import date
from unittest.mock import Mock, patch

from services.login_profile import CREATE_ACCOUNT_URL, generate_profile, is_about_you_url
from services.login_otp import complete_email_otp
from test.test_login_otp import CALLBACK, LOGIN_DATA, START, FakeMailbox, FakeSession, Response


class ProfileTests(unittest.TestCase):
    def test_profile_age_bounds_and_leap_day(self):
        for offset, expected in ((0, "2008-02-29"), (1, "2007-02-28"), (40, "1968-02-29")):
            with self.subTest(offset=offset), patch("services.login_profile.secrets.randbelow", return_value=offset):
                profile = generate_profile(date(2028, 2, 29))
                self.assertEqual(profile["birthdate"], expected)
                self.assertRegex(profile["name"], r"^[A-Za-z]{8}$")

    def test_only_the_auth_profile_page_is_recognized(self):
        self.assertTrue(is_about_you_url("https://auth.openai.com/about-you"))
        self.assertFalse(is_about_you_url("https://example.com/about-you"))
        self.assertFalse(is_about_you_url("https://auth.openai.com/workspace"))

    @patch("utils.sentinel.build_sentinel_token", return_value=("profile-token", "cookie"))
    def test_email_verification_completes_profile_and_gets_code(self, sentinel):
        session = FakeSession([
            Response(200, {"continue_url": "https://auth.openai.com/about-you"}),
            Response(200, {"continue_url": CALLBACK}),
        ])
        session.cookies = Mock()
        result = complete_email_otp(
            session, "a@example.com", LOGIN_DATA, {"oai-device-id": "device"}, START, FakeMailbox(["123456"])
        )
        self.assertEqual(result, {"ok": True, "code": "abc123"})
        self.assertEqual(session.posts[1][0], CREATE_ACCOUNT_URL)
        sentinel.assert_called_once_with(session, "device", "oauth_create_account", solve_turnstile=True)

    @patch("utils.sentinel.build_sentinel_token", return_value=("profile-token", ""))
    def test_profile_rejection_stops_without_repeating_submission(self, _):
        session = FakeSession([
            Response(200, {"continue_url": "https://auth.openai.com/about-you"}),
            Response(400, {"error": {"code": "registration_disallowed"}}),
        ])
        result = complete_email_otp(session, "a@example.com", LOGIN_DATA, {}, START, FakeMailbox(["123456"]))
        self.assertEqual(result["error"], "profile_completion_failed_400")
        self.assertEqual(result["detail"]["error"]["code"], "registration_disallowed")
        self.assertEqual(len(session.posts), 2)


if __name__ == "__main__":
    unittest.main()
