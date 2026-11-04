from __future__ import annotations

import imaplib
from datetime import datetime, timedelta

from services.mailbox.base import MailboxError, MailboxProvider, MailMessage, parse_raw_message

PRESET_HOSTS = {
    "qq": "imap.qq.com",
    "gmail": "imap.gmail.com",
    "163": "imap.163.com",
    "outlook": "outlook.office365.com",
}

imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))


class ImapProvider(MailboxProvider):
    """IMAP over SSL, e.g. QQ Mail with an authorization code or Gmail with an app password.

    settings: preset or host, port, username, password, folders (list), max_messages
    """

    kind = "imap"

    def _host(self) -> str:
        host = str(self.settings.get("host") or "").strip()
        if host:
            return host
        preset = str(self.settings.get("preset") or "").strip().lower()
        if preset in PRESET_HOSTS:
            return PRESET_HOSTS[preset]
        raise MailboxError(f"{self.name}: host is empty")

    def _connect(self) -> imaplib.IMAP4_SSL:
        username = str(self.settings.get("username") or "").strip()
        password = str(self.settings.get("password") or "").strip()
        if not username or not password:
            raise MailboxError(f"{self.name}: username/password is empty")
        try:
            client = imaplib.IMAP4_SSL(self._host(), int(self.settings.get("port") or 993), timeout=20)
            client.login(username, password)
        except Exception as exc:
            raise MailboxError(f"{self.name}: IMAP login failed: {exc}") from exc
        try:
            # 163/126 refuse SELECT until the client identifies itself (RFC 2971).
            client._simple_command("ID", '("name" "chatgpt2api" "version" "1.0")')
        except Exception:
            pass
        return client

    def _folders(self) -> list[str]:
        folders = self.settings.get("folders")
        if isinstance(folders, list) and folders:
            return [str(item) for item in folders if str(item or "").strip()]
        return ["INBOX"]

    def fetch_recent(self, address: str, since: datetime) -> list[MailMessage]:
        limit = int(self.settings.get("max_messages") or 15)
        since_day = (since - timedelta(days=1)).strftime("%d-%b-%Y")
        client = self._connect()
        messages: list[MailMessage] = []
        try:
            for folder in self._folders():
                status, _ = client.select(f'"{folder}"', readonly=True)
                if status != "OK":
                    continue
                status, data = client.uid("SEARCH", None, "SINCE", since_day)
                if status != "OK" or not data or not data[0]:
                    continue
                for uid in data[0].split()[-limit:]:
                    status, parts = client.uid("FETCH", uid, "(BODY.PEEK[])")
                    if status != "OK":
                        continue
                    for part in parts or []:
                        if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                            messages.append(parse_raw_message(part[1]))
        except MailboxError:
            raise
        except Exception as exc:
            raise MailboxError(f"{self.name}: IMAP read failed: {exc}") from exc
        finally:
            try:
                client.logout()
            except Exception:
                pass
        return messages

    def test(self) -> dict:
        client = self._connect()
        try:
            result = {}
            for folder in self._folders():
                status, data = client.select(f'"{folder}"', readonly=True)
                result[folder] = int(data[0]) if status == "OK" and data and data[0] else f"select failed: {status}"
            return {"ok": True, "folders": result}
        finally:
            try:
                client.logout()
            except Exception:
                pass
