from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from curl_cffi.requests import Session

from services.proxy_service import proxy_settings


SESSION_URL = "https://chatgpt.com/api/auth/session"
DEFAULT_REFRESH_SKEW = timedelta(hours=1)


class SessionRefreshError(RuntimeError):
    pass


class InvalidSessionTokenError(SessionRefreshError):
    pass


def _clean(value: object) -> str:
    return str(value or "").strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: object) -> datetime | None:
    raw = _clean(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def access_token_expires_at(access_token: str) -> datetime | None:
    parts = _clean(access_token).split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return None
    try:
        exp = int(data.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if exp <= 0:
        return None
    return datetime.fromtimestamp(exp, timezone.utc)


def access_token_needs_refresh(
    account: dict[str, Any],
    skew: timedelta = DEFAULT_REFRESH_SKEW,
) -> bool:
    expires_at = _parse_iso(account.get("access_token_expires_at"))
    if expires_at is None:
        expires_at = access_token_expires_at(_clean(account.get("access_token")))
    if expires_at is None:
        return False
    return expires_at <= _now() + skew


def _extract_account_type(payload: dict[str, Any]) -> str:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    for key in ("planType", "plan_type", "type"):
        value = _clean(account.get(key))
        if value:
            return value
    return ""


def _extract_email(payload: dict[str, Any]) -> str:
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    return _clean(user.get("email") or payload.get("email"))


def _extract_user_id(payload: dict[str, Any]) -> str:
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    return _clean(user.get("id") or payload.get("user_id"))


def _session_cookies(session_token: str) -> str:
    token = _clean(session_token)
    return "; ".join(
        [
            f"__Secure-next-auth.session-token={token}",
            f"next-auth.session-token={token}",
        ]
    )


def refresh_chatgpt_session(account: dict[str, Any]) -> dict[str, Any]:
    session_token = _clean(account.get("session_token") or account.get("sessionToken"))
    if not session_token:
        raise SessionRefreshError("session_token is required")

    session = Session(
        **proxy_settings.build_session_kwargs(impersonate="chrome", verify=True)
    )
    try:
        response = session.get(
            SESSION_URL,
            headers={
                "Accept": "application/json",
                "Cookie": _session_cookies(session_token),
                "Referer": "https://chatgpt.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/145.0.0.0 Safari/537.36"
                ),
            },
            timeout=30,
        )
        if response.status_code in {401, 403}:
            raise InvalidSessionTokenError(
                f"session refresh failed: HTTP {response.status_code}"
            )
        if not response.ok:
            raise SessionRefreshError(
                f"session refresh failed: HTTP {response.status_code}"
            )
        payload = response.json()
    finally:
        session.close()

    if not isinstance(payload, dict):
        raise SessionRefreshError("session refresh payload is invalid")

    access_token = _clean(payload.get("accessToken") or payload.get("access_token"))
    if not access_token:
        raise SessionRefreshError("session refresh did not return accessToken")

    expires_at = access_token_expires_at(access_token)
    updates: dict[str, Any] = {
        "access_token": access_token,
        "session_token": session_token,
        "last_token_refresh_at": _iso(_now()),
    }
    if expires_at is not None:
        updates["access_token_expires_at"] = _iso(expires_at)
    if payload.get("expires"):
        updates["auth_session_expires_at"] = _clean(payload.get("expires"))
    if email := _extract_email(payload):
        updates["email"] = email
    if user_id := _extract_user_id(payload):
        updates["user_id"] = user_id
    if account_type := _extract_account_type(payload):
        updates["type"] = account_type
    return updates
