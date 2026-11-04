import tempfile
import unittest
from pathlib import Path

from services.mailbox.env_config import mailbox_config_from_env
from services.mailbox.service import MailboxService

LEGACY_ENV = {
    "MAILBOX_CF_BASE_URL": "https://mail.example",
    "MAILBOX_CF_ADMIN_PASSWORD": "cfpw",
    "MAILBOX_CF_SITE_PASSWORD": "",
    "MAILBOX_CF_DOMAINS": "040601.xyz, 040601.com",
    "MAILBOX_GMAIL_USERNAME": "me@gmail.com",
    "MAILBOX_GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop",
    "MAILBOX_GMAIL_DOMAINS": "peteroooooooo.com,me@gmail.com",
    "MAILBOX_QQ_USERNAME": "1@qq.com",
    "MAILBOX_QQ_AUTH_CODE": "qqcode",
    "MAILBOX_QQ_DOMAINS": "duck.com",
    "MAILBOX_OTP_TIMEOUT_SECONDS": "90",
    "UNRELATED": "x",
}


class EnvConfigTests(unittest.TestCase):
    def test_legacy_names_get_default_kinds_and_aliases(self):
        config = mailbox_config_from_env(LEGACY_ENV)
        providers = config["providers"]
        self.assertEqual(providers["cf"]["kind"], "cloudflare_temp_email")
        self.assertNotIn("site_password", providers["cf"])
        self.assertEqual(providers["gmail"], {"kind": "imap", "preset": "gmail", "username": "me@gmail.com", "password": "abcdefghijklmnop", "source": "env"})
        self.assertEqual(providers["qq"]["password"], "qqcode")
        self.assertEqual(
            [(r["match"], r["provider"]) for r in config["routes"]],
            [("040601.xyz", "cf"), ("040601.com", "cf"), ("peteroooooooo.com", "gmail"), ("me@gmail.com", "gmail"), ("duck.com", "qq")],
        )
        self.assertEqual(config["otp_timeout_seconds"], 90)

    def test_generic_named_mailbox(self):
        env = {
            "MAILBOXES": "work",
            "MAILBOX_WORK_KIND": "imap",
            "MAILBOX_WORK_HOST": "imap.example.org",
            "MAILBOX_WORK_PORT": "993",
            "MAILBOX_WORK_USERNAME": "u",
            "MAILBOX_WORK_PASSWORD": "p",
            "MAILBOX_WORK_FOLDERS": "INBOX, Junk",
            "MAILBOX_WORK_REQUIRE_RECIPIENT_MATCH": "false",
            "MAILBOX_WORK_DOMAINS": "*",
            "MAILBOX_CF_BASE_URL": "ignored because MAILBOXES lists only work",
        }
        config = mailbox_config_from_env(env)
        self.assertEqual(list(config["providers"]), ["work"])
        work = config["providers"]["work"]
        self.assertEqual(work["folders"], ["INBOX", "Junk"])
        self.assertIs(work["require_recipient_match"], False)
        self.assertEqual(config["routes"], [{"match": "*", "provider": "work", "source": "env"}])

    def test_mailbox_without_kind_is_skipped(self):
        self.assertEqual(mailbox_config_from_env({"MAILBOX_FOO_USERNAME": "x"})["providers"], {})


class EnvMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "mailboxes.json"
        self.service = MailboxService(self.path, env_config=mailbox_config_from_env(LEGACY_ENV))

    def tearDown(self):
        self.tmp.cleanup()

    def test_env_routes_resolve(self):
        self.assertEqual(self.service.resolve("a@040601.xyz").name, "cf")
        self.assertEqual(self.service.resolve("b@peteroooooooo.com").name, "gmail")
        self.assertEqual(self.service.resolve("me@gmail.com").name, "gmail")
        self.assertEqual(self.service.resolve("c@duck.com").name, "qq")
        self.assertIsNone(self.service.resolve("x@proton.me"))

    def test_ui_save_keeps_env_out_of_file_and_allows_extra_routes(self):
        public = self.service.public_settings()
        self.assertEqual(public["providers"]["cf"]["admin_password"], "")
        public["routes"].append({"match": "proton.me", "provider": "gmail"})
        self.service.update_settings(public)
        saved = self.path.read_text(encoding="utf-8")
        self.assertNotIn("cfpw", saved)
        self.assertNotIn("040601.xyz", saved)
        self.assertEqual(self.service.resolve("x@proton.me").name, "gmail")
        self.assertEqual(self.service.resolve("a@040601.xyz").name, "cf")


if __name__ == "__main__":
    unittest.main()
