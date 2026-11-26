"""Register a new ChatGPT account the way chatgpt.com does in a browser.

The OpenAI Platform client used for normal logins answers a new email with a
passwordless sign-up page whose code is never mailed. The ChatGPT web client
does mail it, so new accounts are created through chatgpt.com, and the caller
then logs in with the usual Platform login to get a refresh token.
"""
from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin, urlparse

from services.login_otp import AUTH_BASE, follow_to_auth_code, validate_code_from_mail
from services.login_profile import complete_about_you, is_about_you_url
from services.mailbox.base import MailboxError
from services.openai_session_service import is_cloudflare_challenge
from utils.browser_profile import SEC_CH_UA, SEC_CH_UA_MOBILE, SEC_CH_UA_PLATFORM, USER_AGENT

CHATGPT_BASE = "https://chatgpt.com"


def generate_strong_password(length: int = 16) -> str:
    """Generate a random password meeting OpenAI complexity rules."""
    length = max(12, length)
    lowers = string.ascii_lowercase
    uppers = string.ascii_uppercase
    digits = string.digits
    symbols = "!@#$%^&*-_"
    pool = lowers + uppers + digits + symbols
    chars = [
        secrets.choice(lowers),
        secrets.choice(uppers),
        secrets.choice(digits),
        secrets.choice(symbols),
        *(secrets.choice(pool) for _ in range(length - 4)),
    ]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def _sync_clearance_cookies(session, bundle) -> None:
    if bundle is None or not getattr(bundle, "cookies", None):
        return
    cookies = getattr(session, "cookies", None)
    setter = getattr(cookies, "set", None)
    if not callable(setter):
        return
    for name, value in bundle.cookies.items():
        try:
            setter(name, value, domain=".chatgpt.com")
        except TypeError:
            setter(name, value)


def _chatgpt_request(session, method: str, url: str, *, headers: dict, clearance=None, **kwargs):
    fn = session.post if method.upper() == "POST" else session.get
    if clearance is None:
        return fn(url, headers=headers, **kwargs)
    for attempt in range(2):
        req_headers = clearance.build_headers(headers, target_url=url, upstream=True)
        resp = fn(url, headers=req_headers, **kwargs)
        if resp.status_code == 403 and is_cloudflare_challenge(resp) and attempt == 0:
            bundle = clearance.refresh_clearance(target_url=CHATGPT_BASE, force=True, upstream=True)
            _sync_clearance_cookies(session, bundle)
            continue
        return resp
    return resp


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


def register_account(session, email: str, password: str, mailbox, *, sentinel, clearance=None) -> dict:
    """Create the ChatGPT account when the email has none yet.

    sentinel(device_id, flow) -> (token, oai_sc_cookie).
    Returns {"ok": True, "created": True, "password_set": bool, ...} after sign-up
    (OpenAI decides whether the form asks for a password; when asked without a
    provided password a strong one is generated and returned in "password"),
    {"ok": True, "created": False} when the email already has an account, or
    {"ok": False, "error": ..., "detail": ...}.
    """
    nav = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": USER_AGENT,
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": SEC_CH_UA_MOBILE,
        "sec-ch-ua-platform": SEC_CH_UA_PLATFORM,
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "upgrade-insecure-requests": "1",
    }
    _chatgpt_request(session, "GET", f"{CHATGPT_BASE}/auth/login", headers={**nav, "sec-fetch-site": "none"}, clearance=clearance, timeout=30)
    csrf_resp = _chatgpt_request(
        session,
        "GET",
        f"{CHATGPT_BASE}/api/auth/csrf",
        headers={"accept": "application/json", "user-agent": USER_AGENT, "referer": f"{CHATGPT_BASE}/auth/login"},
        clearance=clearance,
        timeout=30,
    )
    csrf = str(_json(csrf_resp).get("csrfToken") or "")
    if not csrf:
        return _error(csrf_resp, "signup_csrf_failed")
    query = urlencode({"prompt": "login", "screen_hint": "login_or_signup", "login_hint": email})
    signin_resp = _chatgpt_request(
        session,
        "POST",
        f"{CHATGPT_BASE}/api/auth/signin/openai?{query}",
        headers={
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded",
            "origin": CHATGPT_BASE,
            "referer": f"{CHATGPT_BASE}/auth/login",
            "user-agent": USER_AGENT,
        },
        clearance=clearance,
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
        "sec-ch-ua-mobile": SEC_CH_UA_MOBILE,
        "sec-ch-ua-platform": SEC_CH_UA_PLATFORM,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "oai-device-id": device_id,
    }

    used_password = password
    if landed == "/create-account/password":
        if not used_password:
            used_password = generate_strong_password()
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
            json={"password": used_password, "username": email},
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
        _chatgpt_request(
            session,
            "GET",
            last_url,
            headers={**nav, "sec-fetch-site": "same-site", "referer": f"{AUTH_BASE}/"},
            clearance=clearance,
            allow_redirects=True,
            timeout=30,
        )
    except Exception:
        pass
    password_set = landed == "/create-account/password"
    result = {"ok": True, "created": created or password_set, "password_set": password_set}
    if password_set and not password:
        result["password"] = used_password
    return result
