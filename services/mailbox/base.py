from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, message_from_string, policy
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from typing import Callable

CODE_SEMANTIC_PATTERN = re.compile(
    r"(?:code\s*(?:is|:)?|验证码\s*[是为:：]?)\s*[:：]?\s*(\d{6})(?!\d)",
    re.IGNORECASE,
)
CODE_PLAIN_PATTERN = re.compile(r"(?<![\d#])(\d{6})(?!\d)")
OPENAI_SUBJECT_HINTS = ("chatgpt", "openai", "verification code", "验证码", "login code")

# Messages are fetched with this much slack before the login start time,
# because mail servers and this host rarely share a clock.
CLOCK_SKEW = timedelta(seconds=60)


class MailboxError(RuntimeError):
    pass


@dataclass
class MailMessage:
    received_at: datetime | None
    sender: str
    recipients: list[str] = field(default_factory=list)
    subject: str = ""
    text: str = ""

    def is_from_openai(self) -> bool:
        sender = self.sender.lower()
        if "openai" in sender:
            return True
        subject = self.subject.lower()
        return any(hint in subject for hint in OPENAI_SUBJECT_HINTS)

    def addressed_to(self, address: str) -> bool:
        target = address.strip().lower()
        return any(target == item.strip().lower() for item in self.recipients)


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return unescape(re.sub(r"(?s)<[^>]+>", " ", html))


def _body_text(message: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            payload = part.get_content()
        except Exception:
            raw = part.get_payload(decode=True) or b""
            payload = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if content_type == "text/plain" else html).append(str(payload))
    if plain:
        return "\n".join(plain)
    return "\n".join(_html_to_text(item) for item in html)


RECIPIENT_HEADERS = ("To", "Cc", "Delivered-To", "X-Original-To", "X-Forwarded-To", "Envelope-To")


def parse_raw_message(raw: bytes | str, received_at: datetime | None = None) -> MailMessage:
    if isinstance(raw, bytes):
        message = message_from_bytes(raw, policy=policy.default)
    else:
        message = message_from_string(raw, policy=policy.default)
    headers: list[str] = []
    for name in RECIPIENT_HEADERS:
        headers.extend(str(value) for value in (message.get_all(name) or []))
    recipients = [addr for _, addr in getaddresses(headers) if addr]
    if received_at is None and message.get("Date"):
        try:
            received_at = parsedate_to_datetime(str(message.get("Date")))
        except Exception:
            received_at = None
    return MailMessage(
        received_at=_to_utc(received_at),
        sender=str(message.get("From") or ""),
        recipients=recipients,
        subject=str(message.get("Subject") or ""),
        text=_body_text(message),
    )


def extract_code(message: MailMessage) -> str | None:
    for source in (message.subject, message.text):
        match = CODE_SEMANTIC_PATTERN.search(source or "")
        if match:
            return match.group(1)
    for source in (message.subject, message.text):
        match = CODE_PLAIN_PATTERN.search(source or "")
        if match:
            return match.group(1)
    return None


class MailboxProvider(ABC):
    kind = ""

    def __init__(self, name: str, settings: dict):
        self.name = name
        self.settings = settings

    @abstractmethod
    def fetch_recent(self, address: str, since: datetime) -> list[MailMessage]:
        """Return messages received after `since` that may belong to `address`."""

    def test(self) -> dict:
        """Check credentials; return a short summary for the settings page."""
        messages = self.fetch_recent("", datetime.now(timezone.utc) - timedelta(days=7))
        return {"ok": True, "recent_messages": len(messages)}


def pick_code(
    messages: list[MailMessage],
    address: str,
    since: datetime,
    *,
    require_recipient_match: bool,
    exclude: set[str] | None = None,
) -> str | None:
    floor = since - CLOCK_SKEW
    candidates: list[tuple[datetime, str]] = []
    for message in messages:
        if not message.is_from_openai():
            continue
        if message.received_at is not None and message.received_at < floor:
            continue
        if require_recipient_match and address and not message.addressed_to(address):
            continue
        code = extract_code(message)
        if not code or (exclude and code in exclude):
            continue
        candidates.append((message.received_at or datetime.min.replace(tzinfo=timezone.utc), code))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def wait_for_code(
    provider: MailboxProvider,
    address: str,
    since: datetime,
    *,
    timeout: float = 120,
    interval: float = 5,
    require_recipient_match: bool = True,
    exclude: set[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    deadline = time.monotonic() + timeout
    last_error = ""
    while True:
        try:
            messages = provider.fetch_recent(address, since)
            code = pick_code(
                messages,
                address,
                since,
                require_recipient_match=require_recipient_match,
                exclude=exclude,
            )
            if code:
                return code
        except MailboxError as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            detail = f"; last error: {last_error}" if last_error else ""
            raise MailboxError(f"no OpenAI code for {address} in {int(timeout)}s via {provider.name}{detail}")
        sleep(interval)
