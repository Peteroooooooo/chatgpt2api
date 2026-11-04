from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from threading import Lock

from services.mailbox.base import MailboxError, MailboxProvider, MailMessage, parse_raw_message

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailApiProvider(MailboxProvider):
    """Gmail API with an OAuth refresh token (scope gmail.readonly).

    settings: client_id, client_secret, refresh_token, max_results (optional)
    """

    kind = "gmail_api"

    def __init__(self, name: str, settings: dict):
        super().__init__(name, settings)
        self._lock = Lock()
        self._access_token = ""
        self._expires_at = 0.0

    def _token(self) -> str:
        from curl_cffi import requests

        with self._lock:
            if self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
            fields = {key: str(self.settings.get(key) or "").strip() for key in ("client_id", "client_secret", "refresh_token")}
            missing = [key for key, value in fields.items() if not value]
            if missing:
                raise MailboxError(f"{self.name}: missing {', '.join(missing)}")
            try:
                response = requests.post(TOKEN_URL, data={**fields, "grant_type": "refresh_token"}, timeout=20)
            except Exception as exc:
                raise MailboxError(f"{self.name}: token request failed: {exc}") from exc
            data = response.json() if response.text else {}
            if response.status_code != 200 or not data.get("access_token"):
                detail = data.get("error_description") or data.get("error") or response.text[:200]
                raise MailboxError(f"{self.name}: token refresh HTTP {response.status_code}: {detail}")
            self._access_token = str(data["access_token"])
            self._expires_at = time.time() + int(data.get("expires_in") or 3600)
            return self._access_token

    def _get(self, path: str, params: dict | None = None) -> dict:
        from curl_cffi import requests

        try:
            response = requests.get(
                f"{API_BASE}{path}",
                headers={"authorization": f"Bearer {self._token()}"},
                params=params,
                timeout=20,
            )
        except MailboxError:
            raise
        except Exception as exc:
            raise MailboxError(f"{self.name}: request failed: {exc}") from exc
        if response.status_code != 200:
            raise MailboxError(f"{self.name}: HTTP {response.status_code} {response.text[:200]}")
        return response.json()

    def fetch_recent(self, address: str, since: datetime) -> list[MailMessage]:
        after = int(since.timestamp()) - 120
        query = f"after:{after} (from:openai.com OR subject:ChatGPT OR subject:OpenAI)"
        if address:
            query += f" {{to:{address} deliveredto:{address}}}"
        listing = self._get("/messages", {"q": query, "maxResults": str(int(self.settings.get("max_results") or 10))})
        messages: list[MailMessage] = []
        for item in listing.get("messages") or []:
            detail = self._get(f"/messages/{item['id']}", {"format": "raw"})
            raw = base64.urlsafe_b64decode(str(detail.get("raw") or "") + "==")
            received = None
            if detail.get("internalDate"):
                received = datetime.fromtimestamp(int(detail["internalDate"]) / 1000, tz=timezone.utc)
            messages.append(parse_raw_message(raw, received))
        return messages

    def test(self) -> dict:
        profile = self._get("/profile")
        return {"ok": True, "mailbox": profile.get("emailAddress"), "total_messages": profile.get("messagesTotal")}
