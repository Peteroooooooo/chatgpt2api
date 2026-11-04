from __future__ import annotations

from datetime import datetime, timezone

from services.mailbox.base import MailboxError, MailboxProvider, MailMessage, parse_raw_message


def _parse_created_at(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class CloudflareTempEmailProvider(MailboxProvider):
    """dreamhunter2333/cloudflare_temp_email, read through its admin API.

    settings: base_url, admin_password, site_password (optional), page_size (optional)
    """

    kind = "cloudflare_temp_email"

    def _headers(self) -> dict[str, str]:
        admin_password = str(self.settings.get("admin_password") or "").strip()
        if not admin_password:
            raise MailboxError(f"{self.name}: admin_password is empty")
        headers = {"x-admin-auth": admin_password, "accept": "application/json"}
        site_password = str(self.settings.get("site_password") or "").strip()
        if site_password:
            headers["x-custom-auth"] = site_password
        return headers

    def fetch_recent(self, address: str, since: datetime) -> list[MailMessage]:
        from curl_cffi import requests

        base_url = str(self.settings.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            raise MailboxError(f"{self.name}: base_url is empty")
        params = {"limit": str(int(self.settings.get("page_size") or 20)), "offset": "0"}
        if address:
            params["address"] = address.strip().lower()
        try:
            response = requests.get(f"{base_url}/admin/mails", headers=self._headers(), params=params, timeout=20)
        except Exception as exc:
            raise MailboxError(f"{self.name}: request failed: {exc}") from exc
        if response.status_code != 200:
            raise MailboxError(f"{self.name}: HTTP {response.status_code} {response.text[:200]}")
        try:
            data = response.json()
        except Exception as exc:
            raise MailboxError(f"{self.name}: invalid JSON") from exc
        rows = data.get("results") if isinstance(data, dict) else data
        messages: list[MailMessage] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            raw = row.get("raw") or row.get("source") or ""
            if not raw:
                continue
            message = parse_raw_message(str(raw), _parse_created_at(row.get("created_at")))
            row_address = str(row.get("address") or "").strip()
            if row_address and row_address.lower() not in {item.lower() for item in message.recipients}:
                message.recipients.append(row_address)
            messages.append(message)
        return messages
