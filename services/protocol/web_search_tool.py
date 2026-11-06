from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from utils.answer_markup import clean_private_annotations

WEB_SEARCH_TOOL_TYPES = {"web_search", "web_search_preview", "web_search_preview_2025_03_11"}
SEARCH_CHAT_MODEL_PREFIXES = (
    "gpt-4o-search-preview",
    "gpt-4o-mini-search-preview",
    "gpt-5-search-api",
)


def _tool_type(tool: object) -> str:
    return str(tool.get("type") or "").strip() if isinstance(tool, dict) else ""


def has_web_search_tool(body: dict[str, Any]) -> bool:
    tools = body.get("tools")
    if isinstance(tools, list):
        return any(_tool_type(tool) in WEB_SEARCH_TOOL_TYPES for tool in tools)
    tool_choice = body.get("tool_choice")
    return _tool_type(tool_choice) in WEB_SEARCH_TOOL_TYPES


def is_web_search_chat_request(body: dict[str, Any]) -> bool:
    model = str(body.get("model") or "").strip()
    return (
        has_web_search_tool(body)
        or isinstance(body.get("web_search_options"), dict)
        or any(
            model == prefix or model.startswith(f"{prefix}-")
            for prefix in SEARCH_CHAT_MODEL_PREFIXES
        )
    )


def has_unsupported_tools(body: dict[str, Any], allowed_types: set[str]) -> bool:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return False
    return any(_tool_type(tool) not in allowed_types for tool in tools if isinstance(tool, dict))


def message_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("input_text") or "").strip()
            else:
                text = ""
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return ""


def search_query_from_messages(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        text = message_text(message.get("content"))
        if text:
            return text
    return ""


def search_prompt_from_messages(messages: list[dict[str, Any]]) -> str:
    """The last user message, after any system / developer instructions (answer style, focus)."""
    query = search_query_from_messages(messages)
    if not query:
        return ""
    instructions = [
        text
        for message in messages
        if str(message.get("role") or "").strip().lower() in {"system", "developer"}
        and (text := message_text(message.get("content")))
    ]
    return "\n\n".join([*instructions, query])


def clean_search_text(text: str) -> str:
    return clean_private_annotations(text)


def normalized_sources(result: dict[str, Any]) -> list[dict[str, str]]:
    sources = result.get("sources")
    if not isinstance(sources, list):
        return []
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        output.append({"title": title, "url": url, "snippet": snippet})
    return output


def text_with_url_citations(result: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    text = clean_search_text(str(result.get("answer") or ""))
    annotations: list[dict[str, Any]] = []
    sources = normalized_sources(result)
    if sources:
        text = text.rstrip()
        if text:
            text += "\n\n"
        text += "Sources:\n"
        for index, source in enumerate(sources, start=1):
            title = source["title"] or source["url"]
            line_prefix = f"{index}. {title}"
            text += line_prefix
            if source["url"]:
                if source["title"]:
                    text += " - "
                start = len(text)
                text += source["url"]
                annotations.append({
                    "type": "url_citation",
                    "start_index": start,
                    "end_index": len(text),
                    "url": source["url"],
                    "title": source["title"] or source["url"],
                })
            text += "\n"
    return text.strip(), annotations


def run_web_search(query: str, model: object = None) -> dict[str, Any]:
    """Search to the end with the request's model (or the configured search model)."""
    from services.protocol.openai_search import search_model_for, search_with_account

    result, _token = search_with_account(query, search_model_for(model))
    return result


def stream_web_search(query: str, model: object = None) -> Iterator[dict[str, Any]]:
    """Search events as the answer is written: {"type": "delta", "text"}, then {"type": "done", "result"}."""
    from services.protocol.openai_search import search_events, search_model_for

    return search_events(query, search_model_for(model))
