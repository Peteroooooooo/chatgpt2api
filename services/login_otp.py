from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

from services.mailbox.base import MailboxError

AUTH_BASE = "https://auth.openai.com"
SEND_OTP_URL = f"{AUTH_BASE}/api/accounts/email-otp/send"
VALIDATE_OTP_URL = f"{AUTH_BASE}/api/accounts/email-otp/validate"
MAX_REDIRECTS = 8


def code_from_url(url: str) -> str:
    try:
        return str((parse_qs(urlparse(url).query).get("code") or [""])[0]).strip()
    except Exception:
        return ""


def follow_to_auth_code(session, start_url: str, headers: dict) -> tuple[str, str]:
    """Follow redirects from start_url until a callback URL with ?code= appears.

    Returns (code, last_url). code is empty when the chain ends on a page
    (for example a workspace picker) instead of the callback.
    """
    url = urljoin(AUTH_BASE + "/", start_url)
    for _ in range(MAX_REDIRECTS):
        code = code_from_url(url)
        if code and "state=" in url:
            return code, url
        response = session.get(url, headers=headers, allow_redirects=False, timeout=30)
        location = response.headers.get("Location") or response.headers.get("location") or ""
        if response.status_code not in (301, 302, 303, 307, 308) or not location:
            return "", url
        url = urljoin(url, location)
    return code_from_url(url), url


def complete_email_otp(
    session,
    email: str,
    login_data: dict,
    headers: dict,
    started_at: datetime,
    mailbox,
) -> dict:
    """Finish a login that stopped at the email OTP page.

    Returns {"ok": True, "code": <oauth code>} or {"ok": False, "error": ..., "detail": ...}.
    """
    if mailbox.resolve(email) is None:
        return {"ok": False, "error": "need_verification_code", "detail": {"reason": "no mailbox route", "email": email}}

    page_headers = {**headers, "accept": "text/html,application/xhtml+xml,*/*;q=0.8", "sec-fetch-mode": "navigate"}
    continue_url = str(login_data.get("continue_url") or "").strip()
    if continue_url:
        # Opening the verification page is what makes OpenAI send the code.
        try:
            session.get(urljoin(AUTH_BASE + "/", continue_url), headers=page_headers, timeout=30)
        except Exception:
            pass

    otp_headers = {**headers, "referer": f"{AUTH_BASE}/email-verification"}
    rejected: set[str] = set()
    for attempt in range(2):
        try:
            otp = mailbox.wait_for_code(email, started_at, exclude=rejected)
        except MailboxError as exc:
            if attempt == 0:
                try:
                    session.get(SEND_OTP_URL, headers=otp_headers, timeout=30)
                except Exception:
                    pass
                continue
            return {"ok": False, "error": "otp_not_received", "detail": {"reason": str(exc)}}

        response = session.post(VALIDATE_OTP_URL, headers=otp_headers, json={"code": otp}, timeout=30)
        try:
            data = response.json() if response.text else {}
        except Exception:
            data = {}
        if response.status_code != 200:
            rejected.add(otp)
            if attempt == 0:
                continue
            return {"ok": False, "error": f"otp_validate_failed_{response.status_code}", "detail": data or response.text[:300]}

        next_url = str((data or {}).get("continue_url") or "").strip()
        if not next_url:
            return {"ok": False, "error": "otp_no_continue_url", "detail": data}
        code, last_url = follow_to_auth_code(session, next_url, page_headers)
        if not code:
            page = (data or {}).get("page") if isinstance(data, dict) else None
            return {"ok": False, "error": "otp_no_auth_code", "detail": {"last_url": last_url[:300], "page": page}}
        return {"ok": True, "code": code}
    return {"ok": False, "error": "otp_not_received", "detail": {}}
