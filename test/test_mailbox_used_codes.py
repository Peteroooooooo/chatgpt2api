import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from services.mailbox.base import MailMessage
from services.mailbox.service import MailboxService

ENV = {
    "providers": {"box": {"kind": "imap", "host": "imap.example", "username": "u", "password": "p"}},
    "routes": [{"match": "example.com", "provider": "box"}],
}


class UsedCodeTests(unittest.TestCase):
    def test_submitted_code_is_not_returned_again(self):
        with TemporaryDirectory() as tmp:
            service = MailboxService(Path(tmp) / "mailboxes.json", ENV)
            now = datetime.now(timezone.utc)
            old = MailMessage(now, "OpenAI <noreply@tm.openai.com>", ["a@example.com"], "code 111111", "")
            new = MailMessage(now, "OpenAI <noreply@tm.openai.com>", ["a@example.com"], "code 222222", "")
            provider = service.resolve("a@example.com")
            with mock.patch.object(type(provider), "fetch_recent", side_effect=[[old], [old, new]]), mock.patch("time.sleep"):
                self.assertEqual(service.wait_for_code("a@example.com", now), "111111")
                service.mark_used("A@example.com", "111111")
                self.assertEqual(service.wait_for_code("a@example.com", now), "222222")


if __name__ == "__main__":
    unittest.main()
