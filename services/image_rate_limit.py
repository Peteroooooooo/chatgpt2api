"""Recognise "this account hit its limit" answers from ChatGPT on the image path.

ChatGPT says an account is limited in two ways:
- an HTTP 429 on one of the account's own calls before the image starts
  (sentinel chat-requirements, f/conversation/prepare, f/conversation, files,
  codex/responses), sometimes with Retry-After or a clears_in / resets_in_seconds
  / resets_at field in the JSON body;
- a short assistant text instead of an image, e.g. "You've hit the free plan limit
  for image generations requests. You can create more images when the limit resets
  in 23 hours and 59 minutes." (or the Chinese equivalent).

Polling 429s (the conversation document is not committed yet) are handled inside
the poller and never reach here. Cloudflare's own 429 page (error 1015) is about
the egress IP, not the account, so it is not treated as an account limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from utils.helper import UpstreamHTTPError


@dataclass(frozen=True)
class ImageRateLimit:
    reason: str
    restore_at: datetime | None = None
    quota_exhausted: bool = False


class ImageRateLimitedError(RuntimeError):
    def __init__(self, limit: ImageRateLimit) -> None:
        super().__init__(limit.reason)
        self.limit = limit


# Account-scoped calls made before an image exists. bootstrap (GET /) and the blob
# upload (image_upload) are left out: a 429 there is about the IP / storage, not the account.
_ACCOUNT_CONTEXTS = (
    "chat_requirements_prepare",
    "chat_requirements_finalize",
    "/backend-api/f/conversation",
    "/backend-api/files",
    "/backend-api/codex/responses",
)

_LIMIT_CODES = {"rate_limit_exceeded", "model_cap_exceeded", "usage_limit_reached", "image_gen_limit_reached"}

_LIMIT_TEXT_EN = re.compile(
    r"(?:you(?:'|’)ve|you have)\s+(?:hit|reached|exceeded)\b[^.\n]{0,80}?\blimit"
    r"|\blimit\s+(?:resets|will reset)\b"
    r"|\brate[ -]limit"
    r"|\btoo many requests\b"
    r"|\busage cap\b"
    r"|\bcreate more images when\b",
    re.IGNORECASE,
)
_LIMIT_TEXT_ZH = re.compile(
    r"(?:达到|已达|超出|超过|用完|用尽)[^。\n]{0,20}?(?:上限|限额|限制|额度|次数)"
    r"|(?:上限|限额|限制|额度)[^。\n]{0,12}?(?:将在|会在)[^。\n]{0,24}?重置"
    r"|请求(?:过多|太多|太频繁)"
    r"|额度已用(?:完|尽)"
)
# Refusal wording wins: a content-policy answer must never move the request to another account.
_POLICY_WORDS = ("内容政策", "防护限制", "违反", "moderation", "content policy", "policy", "blocked")
_MAX_LIMIT_MESSAGE_CHARS = 800

_DURATION = re.compile(
    r"(\d+(?:\.\d+)?)\s*(days?|hours?|hrs?|minutes?|mins?|seconds?|secs?|天|个小时|小时|分钟|分|秒)",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "day": 86400, "days": 86400, "天": 86400,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "小时": 3600, "个小时": 3600,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "分钟": 60, "分": 60,
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "秒": 1,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_reset_delay(text: str) -> int | None:
    """Seconds named in text like "resets in 23 hours and 59 minutes" / "2 小时 5 分钟后重置"."""
    total = 0.0
    for amount, unit in _DURATION.findall(str(text or "")):
        total += float(amount) * _UNIT_SECONDS.get(unit.lower(), 0)
    return int(total) if total > 0 else None


def _looks_like_policy_refusal(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in _POLICY_WORDS)


def is_limit_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value or len(value) > _MAX_LIMIT_MESSAGE_CHARS or _looks_like_policy_refusal(value):
        return False
    return bool(_LIMIT_TEXT_EN.search(value) or _LIMIT_TEXT_ZH.search(value))


def rate_limit_from_message(text: str) -> ImageRateLimit | None:
    """An assistant text that says the image allowance is used up (instead of an image)."""
    if not is_limit_text(text):
        return None
    value = str(text).strip()
    delay = parse_reset_delay(value)
    return ImageRateLimit(
        reason=f"upstream limit message: {value[:160]}",
        restore_at=_now() + timedelta(seconds=delay) if delay else None,
        quota_exhausted=True,
    )


def _body_text(body: Any) -> str:
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        parts = []
        for key in ("detail", "message", "error", "code", "type"):
            value = body.get(key)
            if isinstance(value, (str, dict)):
                parts.append(_body_text(value))
        return " ".join(part for part in parts if part)
    return ""


def _find_number(body: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(body, dict):
        for key in keys:
            value = body.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value)
            if isinstance(value, str) and value.strip().isdigit():
                return float(value.strip())
        for value in body.values():
            found = _find_number(value, keys)
            if found is not None:
                return found
    return None


def _find_reset_time(body: Any) -> datetime | None:
    delay = _find_number(body, ("clears_in", "resets_in_seconds", "retry_after", "reset_after_seconds"))
    if delay:
        return _now() + timedelta(seconds=delay)
    stamp = _find_number(body, ("resets_at", "reset_at"))
    if stamp:
        try:
            return datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _find_code(body: Any) -> str:
    if isinstance(body, dict):
        for key in ("code", "type"):
            value = body.get(key)
            if isinstance(value, str) and value in _LIMIT_CODES:
                return value
        for value in body.values():
            code = _find_code(value)
            if code:
                return code
    return ""


def _is_account_context(context: str) -> bool:
    value = str(context or "")
    return any(value == item or value.startswith(item + "/") for item in _ACCOUNT_CONTEXTS)


def _is_edge_page(body: Any) -> bool:
    if not isinstance(body, str):
        return False
    lower = body.lower()
    return "<html" in lower or "cloudflare" in lower or "error 1015" in lower


def rate_limit_from_upstream_error(exc: BaseException) -> ImageRateLimit | None:
    """An HTTP 429 (or a 403 whose body says "limit") on an account-scoped start call."""
    if not isinstance(exc, UpstreamHTTPError):
        return None
    if not _is_account_context(exc.context) or _is_edge_page(exc.body):
        return None
    text = _body_text(exc.body)
    code = _find_code(exc.body)
    if exc.status_code == 429:
        pass
    elif exc.status_code == 403 and (code or is_limit_text(text)):
        pass
    else:
        return None
    restore_at = None
    if exc.retry_after:
        restore_at = _now() + timedelta(seconds=int(exc.retry_after))
    restore_at = restore_at or _find_reset_time(exc.body)
    if restore_at is None:
        delay = parse_reset_delay(text) if is_limit_text(text) else None
        restore_at = _now() + timedelta(seconds=delay) if delay else None
    detail = (code or text or "").strip()[:160]
    return ImageRateLimit(
        reason=f"HTTP {exc.status_code} on {exc.context}" + (f": {detail}" if detail else ""),
        restore_at=restore_at,
        quota_exhausted=False,
    )


def rate_limit_from_sse_event(event: Any) -> ImageRateLimit | None:
    """An SSE error event in the image stream that says the account is limited."""
    if not isinstance(event, dict):
        return None
    error = event.get("error")
    if error is None and event.get("type") == "error":
        error = event.get("message") or event
    if error is None:
        return None
    text = _body_text(error) if isinstance(error, dict) else str(error)
    code = _find_code(error) if isinstance(error, dict) else ""
    if not code and not is_limit_text(text):
        return None
    delay = parse_reset_delay(text)
    reset = _find_reset_time(error) if isinstance(error, dict) else None
    return ImageRateLimit(
        reason=f"upstream stream error: {(code or text)[:160]}",
        restore_at=reset or (_now() + timedelta(seconds=delay) if delay else None),
        quota_exhausted=bool(is_limit_text(text)),
    )
