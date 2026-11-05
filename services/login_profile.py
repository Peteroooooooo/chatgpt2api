"""Complete the profile page encountered after email verification."""
from __future__ import annotations

import secrets
import string
from datetime import date, datetime, timezone
from urllib.parse import urlparse

AUTH_BASE = "https://auth.openai.com"
CREATE_ACCOUNT_URL = f"{AUTH_BASE}/api/accounts/create_account"


def generate_profile(today: date | None = None) -> dict[str, str]:
    """Use eight random letters and an age from 20 through 60, inclusive."""
    today = today or datetime.now(timezone.utc).date()
    age = 20 + secrets.randbelow(41)
    try:
        birthdate = today.replace(year=today.year - age)
    except ValueError:  # February 29 in a non-leap birth year.
        birthdate = today.replace(year=today.year - age, day=28)
    return {
        "name": "".join(secrets.choice(string.ascii_letters) for _ in range(8)),
        "birthdate": birthdate.isoformat(),
    }


def is_about_you_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == "auth.openai.com" and parsed.path.rstrip("/") == "/about-you"


def complete_about_you(session, headers: dict) -> dict:
    """Submit the profile once. Preserve upstream rejection instead of retrying it."""
    from utils.sentinel import build_sentinel_token

    profile_headers = {
        **headers,
        "accept": "application/json",
        "content-type": "application/json",
        "origin": AUTH_BASE,
        "referer": f"{AUTH_BASE}/about-you",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
    }
    try:
        token, cookie = build_sentinel_token(
            session, headers.get("oai-device-id", ""), "oauth_create_account", solve_turnstile=True
        )
        profile_headers["openai-sentinel-token"] = token
        if cookie:
            session.cookies.set("oai-sc", cookie, domain=".openai.com")
        response = session.post(CREATE_ACCOUNT_URL, headers=profile_headers, json=generate_profile(), timeout=30)
    except Exception as exc:
        return {"ok": False, "error": "profile_completion_failed", "detail": {"reason": str(exc)}}
    try:
        data = response.json()
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    if response.status_code != 200:
        return {"ok": False, "error": f"profile_completion_failed_{response.status_code}", "detail": data}
    next_url = str(data.get("continue_url") or "").strip()
    if not next_url:
        return {"ok": False, "error": "profile_no_continue_url", "detail": data}
    return {"ok": True, "continue_url": next_url}
