"""Read the answer of a ChatGPT search conversation from its SSE stream as it arrives.

The conversation stream (``supported_encodings: ["v1"]``) creates messages with
``{"v": {"message": ...}, "c": N}`` and then patches message N with text appends.
A search answer is often written twice at once: hidden fragments (metadata
``is_message_fragment``) and one merged message that carries the whole answer.
Only the non-fragment final messages are the answer.

Search results arrive in the same stream with ``ref_id`` objects, so the
``<Link ref="turn1search3" title="..."/>`` tags in the answer can become real links.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from utils.answer_markup import clean_answer_markup, clean_private_annotations, stable_length

TEXT_PATH = "/message/content/parts/0"
CONVERSATION_ID_RE = re.compile(r'"conversation_id"\s*:\s*"([^"]+)"')


@dataclass
class _Message:
    role: str
    channel: str
    recipient: str
    content_type: str
    fragment: bool
    text: str = ""
    last_op_was_text: bool = False

    @property
    def is_answer(self) -> bool:
        if self.role != "assistant" or self.fragment:
            return False
        if self.channel:
            return self.channel == "final"
        return self.recipient in ("", "all") and self.content_type == "text"


def _message_from(message: dict[str, Any]) -> _Message:
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    parts = content.get("parts") if isinstance(content.get("parts"), list) else []
    return _Message(
        role=str((message.get("author") or {}).get("role") or ""),
        channel=str(message.get("channel") or "").strip().lower(),
        recipient=str(message.get("recipient") or "").strip().lower(),
        content_type=str(content.get("content_type") or ""),
        fragment=metadata.get("is_message_fragment") is True,
        text="".join(part for part in parts if isinstance(part, str)),
    )


def _ref_key(ref_id: dict[str, Any]) -> str:
    turn, kind, index = ref_id.get("turn_index"), ref_id.get("ref_type"), ref_id.get("ref_index")
    if turn is None or not kind or index is None:
        return ""
    return f"turn{turn}{kind}{index}"


class SearchAnswerStream:
    """Feed SSE payloads in order; each call returns the answer text that became final."""

    def __init__(self) -> None:
        self.conversation_id = ""
        self.done = False
        self.answer = ""
        self.mismatch = False
        self._messages: dict[int, _Message] = {}
        self._last_index: int | None = None
        self._refs: dict[str, dict[str, str]] = {}
        # A ref resolves once; later results must not rewrite text that was already sent.
        self._resolved: dict[str, str] = {}
        self._cited: list[str] = []

    # ── input ────────────────────────────────────────────────
    def feed(self, payload: str) -> str:
        if not payload:
            return ""
        if payload == "[DONE]":
            return self.finish()
        if not self.conversation_id:
            match = CONVERSATION_ID_RE.search(payload)
            if match:
                self.conversation_id = match.group(1)
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return ""
        if '"ref_id"' in payload:
            self._collect_refs(event)
        if isinstance(event, dict):
            self._apply_event(event)
        return self._advance(final=False)

    def finish(self) -> str:
        if self.done:
            return ""
        self.done = True
        return self._advance(final=True)

    def _apply_event(self, event: dict[str, Any]) -> None:
        value = event.get("v")
        index = event.get("c")
        if isinstance(value, dict) and isinstance(value.get("message"), dict):
            if not isinstance(index, int):
                index = max(self._messages, default=-1) + 1
            self._messages[index] = _message_from(value["message"])
            self._last_index = index
            return
        if "v" not in event:
            return
        if not isinstance(index, int):
            index = self._last_index
        message = self._messages.get(index) if index is not None else None
        if message is None:
            return
        self._last_index = index
        self._apply_op(message, event)

    def _apply_op(self, message: _Message, op: dict[str, Any]) -> None:
        path, kind, value = op.get("p"), op.get("o"), op.get("v")
        if path == TEXT_PATH:
            message.last_op_was_text = True
            if kind == "append" and isinstance(value, str):
                message.text += value
            elif kind == "replace" and isinstance(value, str):
                message.text = value
            return
        if path in (None, "") and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    self._apply_op(message, item)
            return
        if path is None and kind is None and isinstance(value, str):
            # A bare value continues the previous operation on this message.
            if message.last_op_was_text:
                message.text += value
            return
        message.last_op_was_text = False

    def _collect_refs(self, value: Any) -> None:
        if isinstance(value, dict):
            ref_id = value.get("ref_id")
            url = str(value.get("url") or "").strip()
            if isinstance(ref_id, dict) and url.startswith(("http://", "https://")):
                key = _ref_key(ref_id)
                if key and key not in self._refs:
                    self._refs[key] = {
                        "title": str(value.get("title") or "").strip(),
                        "url": url,
                        "snippet": str(value.get("snippet") or "").strip(),
                    }
            for item in value.values():
                if isinstance(item, (dict, list)):
                    self._collect_refs(item)
        elif isinstance(value, list):
            for item in value:
                self._collect_refs(item)

    # ── output ───────────────────────────────────────────────
    def _link_url(self, ref: str) -> str:
        if ref not in self._resolved:
            self._resolved[ref] = (self._refs.get(ref) or {}).get("url", "")
            url = self._resolved[ref]
            if url and url not in self._cited:
                self._cited.append(url)
        return self._resolved[ref]

    def raw_text(self) -> str:
        texts = [message.text for _, message in sorted(self._messages.items()) if message.is_answer]
        return "\n\n".join(text for text in texts if text.strip())

    def _advance(self, *, final: bool) -> str:
        raw = self.raw_text()
        if not final:
            raw = raw[:stable_length(raw)]
        text = clean_private_annotations(clean_answer_markup(raw, self._link_url, normalize=True))
        if text.startswith(self.answer):
            delta = text[len(self.answer):]
        elif final:
            # Cleaning changed text that was already sent; send what follows the shared part.
            self.mismatch = True
            delta = text[len(os.path.commonprefix([text, self.answer])):]
        else:
            return ""
        self.answer += delta
        return delta

    def sources(self) -> list[dict[str, str]]:
        """Search results seen in the stream; the ones the answer links to come first."""
        by_url = {item["url"]: item for item in self._refs.values()}
        ordered = [by_url[url] for url in self._cited if url in by_url]
        ordered += [item for url, item in by_url.items() if url not in self._cited]
        return [dict(item) for item in ordered]
