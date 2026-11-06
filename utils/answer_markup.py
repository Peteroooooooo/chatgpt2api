"""Remove ChatGPT web UI markup from assistant answers.

ChatGPT's web client renders rich layout tags (``<box>``, ``<row>``, ``<col>``,
``<title>``, ``<badge>``, ``<text>``, ``<divider/>``), entity chips
(``<Entity value="X" .../>``), citation pointers (``<Cite ref="turn1search2"/>``),
source links (``<Link ref="turn1search2" title="X"/>``) and widgets such as
``<AsyncImage .../>`` inside answer text. Some answers also carry interactive widgets:
``{@body const [x, setX] = DIL.useState(...)}`` declarations followed by ``{#if}`` /
``{#each}`` template blocks with radio buttons, check boxes and buttons (follow-up
questions, feedback surveys). API callers only get the raw text, so all of this is noise.
Fenced code blocks and inline code spans are left untouched.
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable

_ATTR_VALUE = r"""(?:"[^"]*"|'[^']*'|\{(?:[^{}]|\{[^{}]*\})*\}|[^\s"'{}<>/]+)"""
_ATTR = rf"\s+[A-Za-z_:][\w:.-]*(?:\s*=\s*{_ATTR_VALUE})?"
_ATTRS = rf"(?:{_ATTR})*"
_LAYOUT_TAGS = "box|row|col|grid-item|grid|stack|title|caption|badge|text|bold|icon|spacer|divider"

_ENTITY_SELF_CLOSING_RE = re.compile(rf"<Entity(?P<attrs>{_ATTRS})\s*/>")
_ENTITY_TAG_RE = re.compile(rf"</?Entity{_ATTRS}\s*>")
_CITE_RE = re.compile(rf"[ \t]*<Cite{_ATTRS}\s*/?>|</Cite\s*>")
_LINK_SELF_CLOSING_RE = re.compile(rf"<Link(?P<attrs>{_ATTRS})\s*/>")
_LINK_TAG_RE = re.compile(rf"</?Link{_ATTRS}\s*>")
_LAYOUT_TAG_RE = re.compile(rf"<(?:{_LAYOUT_TAGS}){_ATTRS}\s*/?>|</(?:{_LAYOUT_TAGS})\s*>")
# Other UI widgets (<AsyncImage query="..."/>, <SuggestAutomation label="..."/>): a capitalised
# self-closing tag with at least one attribute. Generics such as List<String> have no attributes.
_WIDGET_RE = re.compile(rf"[ \t]*<[A-Z][A-Za-z0-9]*(?:{_ATTR})+\s*/>")
_WIDGET_GROUP_RE = re.compile(rf"</?AsyncImageGroup{_ATTRS}\s*>")
# Interactive elements and drawings are dropped with everything inside them; an element that
# is still open drops the rest of the text (more of it is on the way while streaming).
# Plain HTML words such as <button> in prose have no attributes and stay.
_DROPPED_ELEMENT_RES = tuple(
    re.compile(rf"<{name}{attrs}\s*>(?:[\s\S]*?</{name}\s*>|[\s\S]*$)")
    for name, attrs in (
        ("radio-group", _ATTRS),
        ("radio", rf"(?:{_ATTR})+"),
        ("button", rf"(?:{_ATTR})+"),
        ("checkbox", rf"(?:{_ATTR})+"),
        ("svg", rf"(?:{_ATTR})+"),
    )
)
_DROPPED_SELF_CLOSING_RE = re.compile(rf"[ \t]*<(?:radio|button|checkbox|textarea|svg)(?:{_ATTR})+\s*/>")
# A widget block starts with {@body ...} declaration lines, then one {#if}/{#each} block or element.
_GENUI_DECLARATION_RE = re.compile(r"^[ \t]*\{@body\b", re.MULTILINE)
_GENUI_TOKEN_RE = re.compile(
    rf"(?P<close>\{{/(?:if|each|key|await)\s*\}}|</[a-z][a-z0-9-]*\s*>)"
    rf"|(?P<open>\{{#(?:if|each|key|await)\b[^\n]*?\}}|<[a-z][a-z0-9-]*{_ATTRS}\s*>)"
)
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_WHITESPACE_LINE_RE = re.compile(r"^[ \t]+$", re.MULTILINE)
_INDENT_RE = re.compile(r"\n[ \t]+")
# The start of a tag that has not been closed yet, at the end of streamed text.
_OPEN_TAG_TAIL_RE = re.compile(r"<(?:[/A-Za-z][^<>]*)?$")
# A last line that may still become a {@body ...} declaration.
_OPEN_BRACE_TAIL_RE = re.compile(r"(?:^|\n)([ \t]*\{[^\n]*)$")
MAX_HELD_TAG_CHARS = 4000


def _matching_brace(text: str, start: int) -> int:
    """Index of the brace closing the one at ``start``, skipping quoted strings; -1 if open."""
    depth = 0
    quote = ""
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _genui_block_end(text: str, start: int) -> int:
    """End of the widget block whose {@body ...} declarations begin at ``start``."""
    index = start
    while match := _GENUI_DECLARATION_RE.match(text, index):
        close = _matching_brace(text, text.index("{", match.start()))
        if close < 0:
            return len(text)
        index = close + 1
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
    body_start = index
    depth = 0
    for token in _GENUI_TOKEN_RE.finditer(text, body_start):
        if depth == 0 and token.start() != body_start:
            break
        depth += -1 if token.group("close") else 1
        if depth <= 0:
            return token.end()
    else:
        if depth > 0:
            return len(text)
    return body_start


def _drop_genui_blocks(text: str) -> str:
    if "{@body" not in text:
        return text
    pieces: list[str] = []
    position = 0
    while match := _GENUI_DECLARATION_RE.search(text, position):
        pieces.append(text[position:match.start()])
        position = _genui_block_end(text, match.start())
    pieces.append(text[position:])
    return "".join(pieces)


def _attr(attrs: str, name: str) -> str:
    found = re.search(rf"""\b{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|\{{\s*["']([^"']*)["']\s*\}})""", attrs or "")
    if not found:
        return ""
    return html.unescape(next((group for group in found.groups() if group is not None), ""))


def _clean_prose(text: str, link_url: Callable[[str], str]) -> str:
    def link(match: re.Match[str]) -> str:
        attrs = match.group("attrs") or ""
        title = _attr(attrs, "title")
        url = link_url(_attr(attrs, "ref"))
        if url:
            return f"[{title or url}]({url})"
        return title

    text = _ENTITY_SELF_CLOSING_RE.sub(lambda match: _attr(match.group("attrs"), "value"), text)
    text = _ENTITY_TAG_RE.sub("", text)
    text = _CITE_RE.sub("", text)
    text = _LINK_SELF_CLOSING_RE.sub(link, text)
    text = _LINK_TAG_RE.sub("", text)
    for element in _DROPPED_ELEMENT_RES:
        text = element.sub("", text)
    text = _DROPPED_SELF_CLOSING_RE.sub("", text)
    text = _WIDGET_RE.sub("", text)
    text = _WIDGET_GROUP_RE.sub("", text)
    return _strip_layout_tags(text)


def _strip_layout_tags(text: str) -> str:
    """Drop layout tags; text inside a container loses the indentation the layout gave it
    (four leading spaces would turn it into a Markdown code block)."""
    pieces: list[str] = []
    depth = 0
    last = 0
    for match in _LAYOUT_TAG_RE.finditer(text):
        segment = text[last:match.start()]
        pieces.append(_INDENT_RE.sub("\n", segment) if depth else segment)
        tag = match.group(0)
        if tag.startswith("</"):
            depth = max(0, depth - 1)
        elif not tag.endswith("/>"):
            depth += 1
        last = match.end()
    segment = text[last:]
    pieces.append(_INDENT_RE.sub("\n", segment) if depth else segment)
    return "".join(pieces)


def _clean_outside_inline_code(text: str, link_url: Callable[[str], str]) -> str:
    parts: list[str] = []
    last = 0
    for match in _INLINE_CODE_RE.finditer(text):
        parts.append(_clean_prose(text[last:match.start()], link_url))
        parts.append(match.group(0))
        last = match.end()
    parts.append(_clean_prose(text[last:], link_url))
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


def clean_answer_markup(
    text: str,
    link_url: Callable[[str], str] | None = None,
    *,
    normalize: bool = False,
) -> str:
    """Strip ChatGPT web UI tags from ``text``; code blocks are kept verbatim.

    ``link_url`` maps a search reference id (``turn953555search3``) to its URL, so
    ``<Link ref=... title=.../>`` becomes a Markdown link instead of bare text.
    Whitespace left behind by removed tags is tidied only in prose that had tags,
    or in all prose when ``normalize`` is set (streaming needs the same result
    whether or not a tag has arrived yet).
    """
    text = str(text or "")
    if "<" not in text and "{@body" not in text and not normalize:
        return text
    resolve = link_url or (lambda _ref: "")
    pieces: list[str] = []
    for is_code, chunk in _split_fenced_code(text):
        if is_code:
            pieces.append(chunk)
            continue
        cleaned = _drop_genui_blocks(chunk)
        cleaned = _clean_outside_inline_code(cleaned, resolve) if "<" in cleaned else cleaned
        if normalize or cleaned != chunk:
            cleaned = _WHITESPACE_LINE_RE.sub("", cleaned)
            cleaned = _BLANK_RUN_RE.sub("\n\n", cleaned)
        pieces.append(cleaned)
    return "".join(pieces)


def stable_length(text: str) -> int:
    """How much of a still-growing answer can be cleaned without cutting a tag in half."""
    end = len(text)
    tail = _OPEN_TAG_TAIL_RE.search(text)
    if tail and end - tail.start() <= MAX_HELD_TAG_CHARS:
        end = tail.start()
    brace = _OPEN_BRACE_TAIL_RE.search(text, 0, end)
    if brace and "}" not in brace.group(1) and end - brace.start(1) <= MAX_HELD_TAG_CHARS:
        end = brace.start(1)
    annotation = text.rfind("", 0, end)
    if annotation >= 0 and "" not in text[annotation:end]:
        end = annotation
    return end


def _readable_annotation_part(parts: list[str]) -> str:
    for part in parts:
        value = part.strip()
        lower = value.lower()
        if value and not (lower.startswith(("turn", "source", "sources")) or re.fullmatch(r"\d+", value)):
            return value
    return ""


def clean_private_annotations(text: str) -> str:
    """Replace ChatGPT's private-use annotation markers (U+E200 … U+E201) with readable text."""

    def replace_annotation(match: re.Match[str]) -> str:
        parts = [part.strip() for part in match.group(1).split("")]
        kind = (parts[0] if parts else "").lower()
        data = parts[1:]
        if kind == "url":
            label = data[0] if data else ""
            url = data[1] if len(data) > 1 else ""
            if label and url.startswith(("http://", "https://")):
                return f"{label} ({url})"
            return label or url
        return _readable_annotation_part(data)

    text = re.sub(r"([^]*)", replace_annotation, str(text or ""))
    text = re.sub(r"[^]*$", "", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", text).strip()
