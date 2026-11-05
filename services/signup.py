"""Register a new ChatGPT account the way chatgpt.com does in a browser.

The OpenAI Platform client used for normal logins answers a new email with a
passwordless sign-up page whose code is never mailed. The ChatGPT web client
does mail it, so new accounts are created through chatgpt.com, and the caller
then logs in with the usual Platform login to get a refresh token.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlparse

from services.login_otp import AUTH_BASE, follow_to_auth_code, validate_code_from_mail
from services.login_profile import complete_about_you, is_about_you_url
from services.mailbox.base import MailboxError

CHATGPT_BASE = "https://chatgpt.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Chromium";v="145", "Google Chrome";v="145", "Not/A)Brand";v="99"'


def _json(response) -> dict:
    try:
        data = response.json() if response.text else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _error(response, prefix: str) -> dict:
    data = _json(response)
    code = (data.get("error") or {}).get("code") if isinstance(data.get("error"), dict) else ""
    if code == "rate_limit_exceeded" or response.status_code == 429:
        return {"ok": False, "error": "rate_limit_exceeded", "detail": data}
    if code == "account_deactivated":
        return {"ok": False, "error": "account_deactivated", "detail": data}
    return {"ok": False, "error": f"{prefix}_{response.status_code}", "detail": data or response.text[:300]}


def _path(url: str) -> str:
    return urlparse(str(url)).path.rstrip("/")


def register_account(session, email: str, password: str, mailbox, *, sentinel) -> dict:
    """Create the ChatGPT account when the email has none yet.

    sentinel(device_id, flow) -> (token, oai_sc_cookie).
    Returns {"ok": True, "created": True, "password_set": bool} after sign-up
    (OpenAI decides whether the form asks for a password; without one the
    account logs in with email codes), {"ok": True, "created": False} when the
    email already has an account, or {"ok": False, "error": ..., "detail": ...}.
    """
    nav = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": USER_AGENT,
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "upgrade-insecure-requests": "1",
    }
    session.get(f"{CHATGPT_BASE}/auth/login", headers={**nav, "sec-fetch-site": "none"}, timeout=30)
    csrf_resp = session.get(
        f"{CHATGPT_BASE}/api/auth/csrf",
        headers={"accept": "application/json", "user-agent": USER_AGENT, "referer": f"{CHATGPT_BASE}/auth/login"},
        timeout=30,
    )
    csrf = str(_json(csrf_resp).get("csrfToken") or "")
    if not csrf:
        return _error(csrf_resp, "signup_csrf_failed")
    query = urlencode({"prompt": "login", "screen_hint": "login_or_signup", "login_hint": email})
    signin_resp = session.post(
        f"{CHATGPT_BASE}/api/auth/signin/openai?{query}",
        headers={
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded",
            "origin": CHATGPT_BASE,
            "referer": f"{CHATGPT_BASE}/auth/login",
            "user-agent": USER_AGENT,
        },
        data=urlencode({"callbackUrl": f"{CHATGPT_BASE}/", "csrfToken": csrf, "json": "true"}),
        timeout=30,
    )
    authorize_url = str(_json(signin_resp).get("url") or "")
    if not authorize_url.startswith(AUTH_BASE):
        return _error(signin_resp, "signup_signin_failed")
    started_at = datetime.now(timezone.utc)
    landing = session.get(
        authorize_url,
        headers={**nav, "sec-fetch-site": "same-site", "referer": f"{CHATGPT_BASE}/"},
        allow_redirects=True,
        timeout=30,
    )
    landed = _path(landing.url)

    device_id = str(session.cookies.get("oai-did") or "")
    api = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": AUTH_BASE,
        "user-agent": USER_AGENT,
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "oai-device-id": device_id,
    }

    if landed == "/create-account/password":
        if not password:
            return {"ok": False, "error": "signup_password_required", "detail": {"page": landed}}
        headers = {**api, "referer": f"{AUTH_BASE}/create-account/password"}
        try:
            token, cookie = sentinel(device_id, "username_password_create")
            headers["openai-sentinel-token"] = token
            if cookie:
                session.cookies.set("oai-sc", cookie, domain=".openai.com")
        except Exception:
            pass
        register_resp = session.post(
            f"{AUTH_BASE}/api/accounts/user/register",
            headers=headers,
            json={"password": password, "username": email},
            timeout=30,
        )
        if register_resp.status_code != 200:
            return _error(register_resp, "signup_register_failed")
        started_at = datetime.now(timezone.utc)
        next_url = str(_json(register_resp).get("continue_url") or f"{AUTH_BASE}/api/accounts/email-otp/send")
        session.get(
            urljoin(AUTH_BASE + "/", next_url),
            headers={**nav, "sec-fetch-site": "same-origin", "referer": f"{AUTH_BASE}/create-account/password"},
            allow_redirects=True,
            timeout=30,
        )
    elif landed == "/email-verification":
        # The page itself asks for the code to be mailed; nothing is sent on load.
        headers = {**api, "referer": f"{AUTH_BASE}/email-verification"}
        try:
            token, cookie = sentinel(device_id, "authorize_continue")
            headers["openai-sentinel-token"] = token
            if cookie:
                session.cookies.set("oai-sc", cookie, domain=".openai.com")
        except Exception:
            pass
        started_at = datetime.now(timezone.utc)
        send_resp = session.post(f"{AUTH_BASE}/api/accounts/passwordless/send-otp", headers=headers, data=b"", timeout=30)
        if send_resp.status_code != 200:
            return _error(send_resp, "signup_send_otp_failed")
    else:
        # /log-in/password, push approval, ...: the email already has an account.
        return {"ok": True, "created": False, "detail": {"page": landed}}

    try:
        validate_resp = validate_code_from_mail(
            session, mailbox, email, started_at, {**api, "referer": f"{AUTH_BASE}/email-verification"}
        )
    except MailboxError as exc:
        return {"ok": False, "error": "otp_not_received", "detail": {"reason": str(exc), "stage": "signup"}}
    if validate_resp.status_code != 200:
        return _error(validate_resp, "otp_validate_failed")
    data = _json(validate_resp)
    next_url = str(data.get("continue_url") or "")
    page = data.get("page") if isinstance(data.get("page"), dict) else {}
    page_headers = {**nav, "sec-fetch-site": "same-origin"}
    auth_code, last_url = follow_to_auth_code(session, next_url, page_headers) if next_url else ("", "")
    created = False
    if not auth_code and (page.get("type") == "about_you" or is_about_you_url(last_url)):
        profile = complete_about_you(session, {**api, "referer": f"{AUTH_BASE}/about-you"})
        if not profile.get("ok"):
            return profile
        created = True
        auth_code, last_url = follow_to_auth_code(session, profile["continue_url"], page_headers)
    if not auth_code:
        return {"ok": False, "error": "signup_no_auth_code", "detail": {"last_url": last_url[:300], "page": page.get("type")}}
    # Finish the chatgpt.com sign-in like the browser does.
    try:
        session.get(last_url, headers={**nav, "sec-fetch-site": "same-site", "referer": f"{AUTH_BASE}/"}, allow_redirects=True, timeout=30)
    except Exception:
        pass
    password_set = landed == "/create-account/password"
    return {"ok": True, "created": created or password_set, "password_set": password_set}
