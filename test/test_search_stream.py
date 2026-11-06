from __future__ import annotations

import json
import unittest
from typing import Any
from unittest import mock

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import openai_v1_chat_complete
from services.protocol.web_search_tool import search_prompt_from_messages
from services.search_stream import SearchAnswerStream

REF = {"turn_index": 7, "ref_type": "search", "ref_index": 3}
ANSWER = (
    "Vite **8.3.6** is the latest release.\n\n"
    '<row align=start gap={3}>\n  <AsyncImage query="Vite logo" aspectRatio="1:1"/>\n'
    '  <box flex="1" gap={1}>\n    **Vite 8.3.6**\n\n    <badge color="success">Nov 5</badge>\n'
    "    - Bug fixes.\n  </box>\n</row>\n"
    'Source: <Link ref="turn7search3" title="Vite on npm"/>.'
)
CLEAN = (
    "Vite **8.3.6** is the latest release.\n\n"
    "**Vite 8.3.6**\n\nNov 5\n- Bug fixes.\n\n"
    "Source: [Vite on npm](https://www.npmjs.com/package/vite)."
)


def _new_message(index: int, message_id: str, role: str, channel: str | None, **metadata: Any) -> str:
    message = {
        "id": message_id,
        "author": {"role": role},
        "channel": channel,
        "recipient": "all",
        "content": {"content_type": "text", "parts": [""]},
        "metadata": metadata,
    }
    return json.dumps({"v": {"message": message, "conversation_id": "conv-1"}, "c": index})


def _append(index: int, text: str, *, wrapped: bool) -> str:
    op = {"p": "/message/content/parts/0", "o": "append", "v": text}
    if wrapped:
        return json.dumps({"p": "", "o": "patch", "v": [{"p": "/message/update_time", "o": "replace", "v": 1.0}, op], "c": index})
    return json.dumps({**op, "c": index})


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _live_shaped_payloads(size: int = 7) -> list[str]:
    """A fragment and the merged answer message receive the same text at the same time."""
    payloads = [
        '"v1"',
        _new_message(0, "u1", "user", None),
        json.dumps({"v": {"message": {
            "id": "t1", "author": {"role": "tool"}, "content": {"content_type": "text", "parts": [""]},
            "metadata": {"search_result_groups": [{"entries": [
                {"url": "https://www.npmjs.com/package/vite", "title": "vite - npm", "ref_id": REF},
                {"url": "https://vite.dev/releases", "title": "Releases", "ref_id": {**REF, "ref_index": 4}},
            ]}]},
        }}, "c": 1}),
        _new_message(2, "frag", "assistant", "final", is_message_fragment=True, is_visually_hidden_from_conversation=True),
        _new_message(3, "merged", "assistant", "final", is_merged_message=True),
    ]
    for chunk in _chunks(ANSWER, size):
        payloads.append(_append(2, chunk, wrapped=False))
        payloads.append(_append(3, chunk, wrapped=True))
    payloads.append(json.dumps({"type": "message_marker", "conversation_id": "conv-1", "marker": "done"}))
    payloads.append("[DONE]")
    return payloads


class SearchAnswerStreamTests(unittest.TestCase):
    def test_streams_only_the_merged_answer_without_markup(self) -> None:
        stream = SearchAnswerStream()
        deltas = [delta for payload in _live_shaped_payloads() if (delta := stream.feed(payload))]

        self.assertGreater(len(deltas), 3)
        self.assertEqual("".join(deltas), CLEAN)
        self.assertEqual(stream.answer, CLEAN)
        self.assertEqual(stream.conversation_id, "conv-1")
        self.assertFalse(stream.mismatch)

    def test_every_chunk_size_gives_the_same_text(self) -> None:
        for size in (1, 2, 3, 5, 11, 40, len(ANSWER)):
            stream = SearchAnswerStream()
            text = "".join(stream.feed(payload) for payload in _live_shaped_payloads(size))
            self.assertEqual(text, CLEAN, f"chunk size {size}")

    def test_no_half_tag_is_ever_sent(self) -> None:
        stream = SearchAnswerStream()
        for payload in _live_shaped_payloads(3):
            stream.feed(payload)
            self.assertNotIn("<", stream.answer)

    def test_sources_list_linked_results_first(self) -> None:
        stream = SearchAnswerStream()
        for payload in _live_shaped_payloads():
            stream.feed(payload)

        urls = [item["url"] for item in stream.sources()]
        self.assertEqual(urls, ["https://www.npmjs.com/package/vite", "https://vite.dev/releases"])

    def test_answer_split_by_tool_calls_joins_both_parts(self) -> None:
        payloads = [
            _new_message(0, "f1", "assistant", "final"),
            _append(0, "First part.", wrapped=False),
            _new_message(1, "call", "assistant", None),
            _new_message(2, "f2", "assistant", "final"),
            _append(2, "Second part.", wrapped=False),
            "[DONE]",
        ]
        stream = SearchAnswerStream()
        text = "".join(stream.feed(payload) for payload in payloads)

        self.assertEqual(text, "First part.\n\nSecond part.")

    def test_reasoning_and_tool_messages_are_not_answer(self) -> None:
        payloads = [
            _new_message(0, "think", "assistant", "analysis"),
            _append(0, "thinking...", wrapped=False),
            _new_message(1, "final", "assistant", "final"),
            _append(1, "Answer.", wrapped=False),
            "[DONE]",
        ]
        stream = SearchAnswerStream()
        self.assertEqual("".join(stream.feed(payload) for payload in payloads), "Answer.")


class SearchResultDedupTests(unittest.TestCase):
    def test_hidden_fragment_is_not_added_to_the_merged_answer(self) -> None:
        def final(message_id: str, text: str, hidden: bool) -> dict[str, Any]:
            return {
                "id": message_id, "author": {"role": "assistant"}, "channel": "final", "create_time": 1.0,
                "content": {"content_type": "text", "parts": [text]},
                "metadata": {"is_visually_hidden_from_conversation": True} if hidden else {"finish_details": {"type": "stop"}},
            }

        conversation = {
            "current_node": "frag",
            "mapping": {
                "u1": {"message": {"author": {"role": "user"}, "content": {"parts": ["q"]}}, "parent": None},
                "merged": {"message": final("merged", "Part one. Part two.", False), "parent": "u1"},
                "frag": {"message": final("frag", "Part two.", True), "parent": "merged"},
            },
        }
        result = object.__new__(OpenAIBackendAPI)._extract_search_result("c1", conversation)

        self.assertEqual(result["answer"], "Part one. Part two.")


class StreamingSearchChatTests(unittest.TestCase):
    def test_chat_stream_sends_text_as_it_arrives_then_sources(self) -> None:
        result = {"answer": "Hello world.", "sources": [{"title": "Ex", "url": "https://example.com", "snippet": ""}]}
        events = iter([
            {"type": "delta", "text": "Hello"},
            {"type": "delta", "text": " world."},
            {"type": "done", "result": result},
        ])
        body = {
            "model": "gpt-6-mini",
            "stream": True,
            "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}],
            "tools": [{"type": "web_search"}],
        }
        with mock.patch("services.protocol.openai_v1_chat_complete.stream_web_search", return_value=events) as search:
            chunks = list(openai_v1_chat_complete.handle(body))

        search.assert_called_once_with("Be brief.\n\nhi", "gpt-6-mini")
        deltas = [chunk["choices"][0]["delta"] for chunk in chunks]
        self.assertEqual(deltas[0], {"role": "assistant", "content": "Hello"})
        self.assertEqual(deltas[1], {"content": " world."})
        self.assertIn("Sources:", deltas[2]["content"])
        self.assertEqual(chunks[2]["search_sources"][0]["url"], "https://example.com")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

    def test_search_prompt_keeps_instructions_and_last_question(self) -> None:
        messages = [
            {"role": "system", "content": "Cite sources."},
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new question"},
        ]
        self.assertEqual(search_prompt_from_messages(messages), "Cite sources.\n\nnew question")


if __name__ == "__main__":
    unittest.main()
