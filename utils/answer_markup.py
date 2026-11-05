"""Remove ChatGPT web UI markup from assistant answers.

ChatGPT's web client renders rich layout tags (``<box>``, ``<row>``, ``<col>``,
``<title>``, ``<badge>``, ``<text>``), entity chips (``<Entity value="X" .../>``)
and citation pointers (``<Cite ref="turn1search2"/>``) inside answer text.
API callers only get the raw text, so these tags are noise. Fenced code
blocks and inline code spans are left untouched.
"""
from __future__ import annotations

import html
import re

_ATTR_VALUE = r"""(?:"[^"]*"|'[^']*'|\{(?:[^{}]|\{[^{}]*\})*\}|[^\s"'{}<>/]+)"""
_ATTRS = rf"(?:\s+[A-Za-z_:][\w:.-]*(?:\s*=\s*{_ATTR_VALUE})?)*"
_LAYOUT_TAGS = "box|row|col|title|badge|text"

_ENTITY_SELF_CLOSING_RE = re.compile(rf"<Entity(?P<attrs>{_ATTRS})\s*/>")
_ENTITY_TAG_RE = re.compile(rf"</?Entity{_ATTRS}\s*>")
_CITE_RE = re.compile(rf"[ \t]*<Cite{_ATTRS}\s*/?>|</Cite\s*>")
_LAYOUT_TAG_RE = re.compile(rf"<(?:{_LAYOUT_TAGS}){_ATTRS}\s*/?>|</(?:{_LAYOUT_TAGS})\s*>")
_ENTITY_VALUE_RE = re.compile(r"""\bvalue\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*["']([^"']*)["']\s*\})""")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_WHITESPACE_LINE_RE = re.compile(r"^[ \t]+$", re.MULTILINE)


def _entity_value(match: re.Match[str]) -> str:
    found = _ENTITY_VALUE_RE.search(match.group("attrs") or "")
    if not found:
        return ""
    return html.unescape(next((group for group in found.groups() if group is not None), ""))


def _clean_prose(text: str) -> str:
    text = _ENTITY_SELF_CLOSING_RE.sub(_entity_value, text)
    text = _ENTITY_TAG_RE.sub("", text)
    text = _CITE_RE.sub("", text)
    return _LAYOUT_TAG_RE.sub("", text)


def _clean_outside_inline_code(text: str) -> str:
    parts: list[str] = []
    last = 0
    for match in _INLINE_CODE_RE.finditer(text):
        parts.append(_clean_prose(text[last:match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_clean_prose(text[last:]))
    return "".join(parts)


def _split_fenced_code(text: str) -> list[tuple[bool, str]]:
    """Split text into (is_code, chunk) pieces; an unclosed fence runs to the end."""
    chunks: list[tuple[bool, str]] = []
    current: list[str] = []
    fence = ""
    for line in text.splitlines(keepends=True):
        match = _FENCE_RE.match(line)
        if not fence:
            if match:
                if current:
                    chunks.append((False, "".join(current)))
                current = [line]
                fence = match.group(1)
            else:
                current.append(line)
            continue
        current.append(line)
        if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                and not line.strip()[len(match.group(1)):].strip():
            chunks.append((True, "".join(current)))
            current = []
            fence = ""
    if current:
        chunks.append((bool(fence), "".join(current)))
    return chunks


def clean_answer_markup(text: str) -> str:
    """Strip ChatGPT web UI tags from ``text``; code blocks are kept verbatim."""
    text = str(text or "")
    if "<" not in text:
        return text
    pieces: list[str] = []
    for is_code, chunk in _split_fenced_code(text):
        if is_code:
            pieces.append(chunk)
            continue
        cleaned = _clean_outside_inline_code(chunk)
        if cleaned != chunk:
            cleaned = _WHITESPACE_LINE_RE.sub("", cleaned)
            cleaned = _BLANK_RUN_RE.sub("\n\n", cleaned)
        pieces.append(cleaned)
    return "".join(pieces)
