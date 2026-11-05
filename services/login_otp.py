from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlparse

from services.mailbox.base import MailboxError

AUTH_BASE = "https://auth.openai.com"
SEND_OTP_URL = f"{AUTH_BASE}/api/accounts/email-otp/send"
VALIDATE_OTP_URL = f"{AUTH_BASE}/api/accounts/email-otp/validate"
PUSH_STATUS_URL = f"{AUTH_BASE}/api/accounts/push-auth/status"
PUSH_VALIDATE_URL = f"{AUTH_BASE}/api/accounts/push-auth/validate"
PUSH_DEVICES_URL = f"{AUTH_BASE}/api/accounts/push-auth/devices/on-login"
PUSH_POLL_SECONDS = 5
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


def _json(response) -> dict:
    try:
        data = response.json() if response.text else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def push_request_id(login_data: dict) -> str:
    """The mfa_request_id of a push-approval page, or "" when there is none."""
    page = login_data.get("page") if isinstance(login_data.get("page"), dict) else {}
    payload = page.get("payload") if isinstance(page.get("payload"), dict) else {}
    request_id = str(payload.get("mfa_request_id") or "").strip()
    if not request_id:
        path = urlparse(str(login_data.get("continue_url") or "")).path
        if "/push-auth-verification/" in path:
            request_id = path.rsplit("/", 1)[-1]
    # "wait" means OpenAI found no signed-in device to send the prompt to.
    return "" if request_id == "wait" else request_id


def _push_devices(session, headers: dict) -> list[str]:
    try:
        response = session.get(PUSH_DEVICES_URL, headers=headers, timeout=30)
    except Exception:
        return []
    devices = _json(response).get("devices") if response.status_code == 200 else None
    return [str(item.get("device_name") or item.get("device_type") or "?") for item in devices or [] if isinstance(item, dict)]


def _wait_for_approval(session, request_id: str, headers: dict, wait_seconds: int, sleep, clock) -> str:
    """Poll until the prompt is answered. Returns approved / denied / expired / timeout / rate_limited / invalid."""
    deadline = clock() + wait_seconds
    while True:
        try:
            response = session.post(PUSH_STATUS_URL, headers=headers, json={"mfa_session_id": request_id}, timeout=30)
        except Exception:
            response = None
        if response is not None:
            data = _json(response)
            if response.status_code == 429:
                return "rate_limited"
            if 400 <= response.status_code < 500:
                code = (data.get("error") or {}).get("code") if isinstance(data.get("error"), dict) else ""
                return "expired" if code == "login_request_expired" else "invalid"
            status = str(data.get("status") or "")
            if status in ("approved", "denied", "expired"):
                return status
        remaining = deadline - clock()
        if remaining <= 0:
            return "timeout"
        sleep(min(PUSH_POLL_SECONDS, remaining))


def complete_push_auth(
    session,
    email: str,
    login_data: dict,
    headers: dict,
    mailbox,
    wait_seconds: int,
    sentinel: Callable[[], dict],
    *,
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Finish a login that stopped at "approve this login in the ChatGPT app".

    Waits up to wait_seconds for the approval, then asks for an email code
    instead when OpenAI offers that and the mailbox route exists.
    sentinel() returns fresh openai-sentinel-token headers for the verify_mfa flow.
    Returns {"ok": True, "code": <oauth code>} or {"ok": False, "error": ..., "detail": ...}.
    """
    page = login_data.get("page") if isinstance(login_data.get("page"), dict) else {}
    payload = page.get("payload") if isinstance(page.get("payload"), dict) else {}
    request_id = push_request_id(login_data)
    base = {**headers, "referer": f"{AUTH_BASE}/push-auth-verification/{request_id or 'wait'}"}
    page_headers = {**base, "accept": "text/html,application/xhtml+xml,*/*;q=0.8", "sec-fetch-mode": "navigate"}

    def signed() -> dict:
        try:
            return {**base, **sentinel()}
        except Exception:
            return dict(base)

    api_headers = signed()
    detail = {
        "challenge_mode": payload.get("challenge_mode"),
        "match_number": payload.get("match_number"),
        "devices": _push_devices(session, api_headers),
        "waited_seconds": wait_seconds if request_id else 0,
    }

    outcome = "no_request_id"
    if request_id and wait_seconds > 0:
        outcome = _wait_for_approval(session, request_id, api_headers, wait_seconds, sleep, clock)
        if outcome == "approved":
            response = session.post(PUSH_VALIDATE_URL, headers=signed(), json={"mfa_session_id": request_id}, timeout=30)
            data = _json(response)
            next_url = str(data.get("continue_url") or "").strip()
            if response.status_code != 200 or not next_url:
                return {"ok": False, "error": f"push_validate_failed_{response.status_code}", "detail": {**detail, "response": data}}
            code, last_url = follow_to_auth_code(session, next_url, page_headers)
            if not code:
                return {"ok": False, "error": "push_no_auth_code", "detail": {**detail, "last_url": last_url[:300], "page": data.get("page")}}
            return {"ok": True, "code": code, "via": "push"}
        if outcome == "denied":
            return {"ok": False, "error": "push_auth_denied", "detail": detail}
    detail["push_outcome"] = outcome

    if payload.get("can_use_email_fallback") is False:
        return {"ok": False, "error": "push_auth_timeout", "detail": {**detail, "reason": "no email fallback offered"}}
    if mailbox.resolve(email) is None:
        return {"ok": False, "error": "push_auth_timeout", "detail": {**detail, "reason": "no mailbox route"}}

    started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    response = session.post(SEND_OTP_URL, headers=signed(), timeout=30)
    data = _json(response)
    if response.status_code != 200:
        return {"ok": False, "error": f"push_email_fallback_failed_{response.status_code}", "detail": {**detail, "response": data}}
    result = complete_email_otp(session, email, data, headers, started_at, mailbox)
    if result.get("ok"):
        result["via"] = "email_fallback"
    return result
