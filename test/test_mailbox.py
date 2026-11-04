import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.mailbox.base import MailboxError, MailboxProvider, extract_code, parse_raw_message, pick_code, wait_for_code
from services.mailbox.service import MailboxService

EMPTY_ENV = {"providers": {}, "routes": []}
NOW = datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc)


def openai_mail(to: str, code: str, when: datetime = NOW, sender: str = "ChatGPT <noreply@tm.openai.com>") -> str:
    return (
        f"From: {sender}\r\n"
        f"To: {to}\r\n"
        f"Subject: Your ChatGPT code is {code}\r\n"
        f"Date: {when.strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
        "--b\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        f"<html><body><p>Enter this temporary verification code to continue:</p><h1>{code}</h1>"
        "<p>Ref #883421</p></body></html>\r\n--b--\r\n"
    )


class FakeProvider(MailboxProvider):
    kind = "fake"

    def __init__(self, batches):
        super().__init__("fake", {})
        self.batches = list(batches)
        self.calls = 0

    def fetch_recent(self, address, since):
        self.calls += 1
        batch = self.batches.pop(0) if self.batches else []
        if isinstance(batch, Exception):
            raise batch
        return batch


class ParseTests(unittest.TestCase):
    def test_extracts_code_from_html_mail(self):
        message = parse_raw_message(openai_mail("a@040601.com", "482913"))
        self.assertEqual(message.recipients, ["a@040601.com"])
        self.assertTrue(message.is_from_openai())
        self.assertEqual(extract_code(message), "482913")

    def test_duck_forwarded_sender_still_counts_as_openai(self):
        raw = openai_mail("me@duck.com", "100200", sender='"ChatGPT" <noreply_at_tm.openai.com_abc@duck.com>')
        message = parse_raw_message(raw)
        self.assertTrue(message.is_from_openai())
        self.assertTrue(message.addressed_to("ME@duck.com"))

    def test_chinese_code_text(self):
        raw = (
            "From: OpenAI <noreply@tm.openai.com>\r\nTo: x@peter.com\r\nSubject: 你的 ChatGPT 验证码\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n订单 20261105 你的验证码为：731042，10 分钟内有效。\r\n"
        )
        self.assertEqual(extract_code(parse_raw_message(raw)), "731042")


class PickTests(unittest.TestCase):
    def test_shared_inbox_picks_only_matching_recipient_and_newest(self):
        messages = [
            parse_raw_message(openai_mail("other@peter.com", "111111", NOW + timedelta(seconds=30))),
            parse_raw_message(openai_mail("me@peter.com", "222222", NOW + timedelta(seconds=10))),
            parse_raw_message(openai_mail("me@peter.com", "333333", NOW + timedelta(seconds=20))),
            parse_raw_message(openai_mail("me@peter.com", "444444", NOW - timedelta(minutes=10))),
        ]
        self.assertEqual(pick_code(messages, "me@peter.com", NOW, require_recipient_match=True), "333333")

    def test_old_mail_is_ignored(self):
        messages = [parse_raw_message(openai_mail("me@peter.com", "444444", NOW - timedelta(minutes=10)))]
        self.assertIsNone(pick_code(messages, "me@peter.com", NOW, require_recipient_match=True))

    def test_excluded_code_is_skipped(self):
        messages = [parse_raw_message(openai_mail("me@peter.com", "555555", NOW))]
        self.assertIsNone(pick_code(messages, "me@peter.com", NOW, require_recipient_match=True, exclude={"555555"}))

    def test_wait_polls_until_code_arrives(self):
        provider = FakeProvider([[], MailboxError("temporary"), [parse_raw_message(openai_mail("me@x.com", "909090", NOW))]])
        code = wait_for_code(provider, "me@x.com", NOW, timeout=60, interval=0, sleep=lambda _: None)
        self.assertEqual(code, "909090")
        self.assertEqual(provider.calls, 3)

    def test_wait_times_out(self):
        provider = FakeProvider([])
        with self.assertRaises(MailboxError):
            wait_for_code(provider, "me@x.com", NOW, timeout=0, interval=0, sleep=lambda _: None)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = MailboxService(Path(self.tmp.name) / "mailboxes.json", env_config=EMPTY_ENV)
        self.service.update_settings({
            "providers": {
                "cf": {"kind": "cloudflare_temp_email", "base_url": "https://mail.example", "admin_password": "s1"},
                "gmail": {"kind": "gmail_api", "client_id": "id", "client_secret": "s2", "refresh_token": "r"},
                "qq": {"kind": "imap", "preset": "qq", "username": "1@qq.com", "password": "s3"},
            },
            "routes": [
                {"match": "040601.com", "provider": "cf"},
                {"match": "peter.com", "provider": "gmail"},
                {"match": "duck.com", "provider": "qq"},
                {"match": "vip@peter.com", "provider": "qq"},
                {"match": "*", "provider": "qq"},
            ],
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_routes(self):
        self.assertEqual(self.service.resolve("a@040601.com").name, "cf")
        self.assertEqual(self.service.resolve("a@mail.peter.com").name, "gmail")
        self.assertEqual(self.service.resolve("VIP@peter.com").name, "qq")
        self.assertEqual(self.service.resolve("x@duck.com").kind, "imap")
        self.assertEqual(self.service.resolve("x@other.org").name, "qq")

    def test_secrets_are_masked_and_kept_on_blank_update(self):
        public = self.service.public_settings()
        self.assertEqual(public["providers"]["cf"]["admin_password"], "")
        self.assertTrue(public["providers"]["cf"]["has_admin_password"])
        self.service.update_settings(public)
        reloaded = MailboxService(self.service.path, env_config=EMPTY_ENV)
        self.assertEqual(reloaded._data["providers"]["cf"]["admin_password"], "s1")
        self.assertEqual(reloaded._data["providers"]["qq"]["password"], "s3")

    def test_unknown_provider_in_route_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.update_settings({"providers": {}, "routes": [{"match": "a.com", "provider": "nope"}]})


if __name__ == "__main__":
    unittest.main()
