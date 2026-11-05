from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from services.openai_backend_api import OpenAIBackendAPI

FIRST_HALF = "First half of the answer. See https://example.com/a for details."
SECOND_HALF = "Second half of the answer."


def _message(
    message_id: str,
    role: str,
    create_time: float,
    text: str = "",
    *,
    channel: str | None = None,
    recipient: str = "all",
    content_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": message_id,
        "author": {"role": role},
        "create_time": create_time,
        "content": {"content_type": content_type, "parts": [text] if text else []},
        "recipient": recipient,
        "status": "finished_successfully",
        "metadata": metadata or {},
    }
    if channel is not None:
        message["channel"] = channel
    return message


def _final(message_id: str, create_time: float, text: str, url: str) -> dict[str, Any]:
    return _message(
        message_id,
        "assistant",
        create_time,
        text,
        channel="final",
        metadata={
            "finish_details": {"type": "stop"},
            "end_turn": True,
            "content_references": [{"type": "grouped_webpages", "items": [{"title": "Ref " + url, "url": url}]}],
            "search_result_groups": [{"entries": [{"title": "Shared", "url": "https://example.com/shared"}]}],
        },
    )


def _split_conversation(stop_after: str | None = None) -> dict[str, Any]:
    """Live-shaped search turn: the reply is split into two final segments."""
    messages = [
        _message("u1", "user", 1.0, "question"),
        _message("a1", "assistant", 2.0, recipient="web.run", channel="analysis"),
        _message("t1", "tool", 3.0, metadata={"search_result_groups": [{"entries": [{"url": "https://tool.example/1"}]}]}),
        _message("a2", "assistant", 4.0, recipient="web.run", channel="analysis"),
        _message("t2", "tool", 5.0),
        _final("f1", 6.0, FIRST_HALF, "https://example.com/first"),
        _message("a3", "assistant", 7.0, recipient="web.run", channel="analysis"),
        _message("t3", "tool", 8.0),
        _message("th", "assistant", 9.0, "thinking summary", content_type="thoughts", channel="analysis"),
        _message("rr", "assistant", 10.0, "Thought for 20s", content_type="reasoning_recap", channel="analysis"),
        _final("f2", 11.0, SECOND_HALF, "https://example.com/second"),
    ]
    if stop_after:
        index = next(i for i, item in enumerate(messages) if item["id"] == stop_after)
        messages = messages[: index + 1]
    mapping: dict[str, Any] = {"root": {"id": "root", "message": None, "parent": None}}
    parent = "root"
    for message in messages:
        mapping[message["id"]] = {"id": message["id"], "message": message, "parent": parent}
        parent = message["id"]
    # An unrelated, newer branch must not leak into the answer.
    mapping["other"] = {
        "id": "other",
        "message": _final("other", 99.0, "Answer from another branch.", "https://other.example"),
        "parent": "u1",
    }
    return {"mapping": mapping, "current_node": messages[-1]["id"]}


def _backend() -> OpenAIBackendAPI:
    return object.__new__(OpenAIBackendAPI)


class SearchAnswerSegmentsTests(unittest.TestCase):
    def test_joins_all_final_segments_in_chain_order(self) -> None:
        result = _backend()._extract_search_result("c1", _split_conversation())

        self.assertEqual(result["answer"], FIRST_HALF + "\n\n" + SECOND_HALF)
        self.assertEqual(result["assistant_message_id"], "f2")
        self.assertEqual(result["status"], "stop")
        self.assertNotIn("thinking summary", result["answer"])
        self.assertNotIn("Thought for", result["answer"])

    def test_sources_come_from_every_segment_without_duplicates(self) -> None:
        result = _backend()._extract_search_result("c1", _split_conversation())
        urls = [item["url"] for item in result["sources"]]

        self.assertIn("https://example.com/first", urls)
        self.assertIn("https://example.com/second", urls)
        self.assertIn("https://example.com/a", urls)
        self.assertEqual(urls.count("https://example.com/shared"), 1)
        self.assertEqual(len(urls), len(set(urls)))
        self.assertNotIn("https://other.example", urls)

    def test_reply_still_running_has_no_done_status(self) -> None:
        result = _backend()._extract_search_result("c1", _split_conversation(stop_after="a3"))

        self.assertEqual(result["answer"], FIRST_HALF)
        self.assertEqual(result["status"], "")

    def test_text_message_without_channel_counts_as_final(self) -> None:
        conversation = _split_conversation()
        for node in conversation["mapping"].values():
            message = node.get("message") or {}
            if message.get("channel") == "final":
                del message["channel"]
        result = _backend()._extract_search_result("c1", conversation)

        self.assertEqual(result["answer"], FIRST_HALF + "\n\n" + SECOND_HALF)

    def test_missing_current_node_falls_back_to_newest_message(self) -> None:
        conversation = _split_conversation()
        del conversation["current_node"]
        result = _backend()._extract_search_result("c1", conversation)

        self.assertEqual(result["answer"], "Answer from another branch.")
        self.assertEqual(result["assistant_message_id"], "other")

    def test_wait_returns_joined_answer_once_stable(self) -> None:
        backend = _backend()
        snapshots = [_split_conversation(stop_after="a3"), _split_conversation()]
        calls = {"count": 0}

        def fake_get(conversation_id: str) -> dict[str, Any]:
            calls["count"] += 1
            return snapshots[min(calls["count"] - 1, len(snapshots) - 1)]

        backend._get_search_conversation = fake_get
        with patch("services.openai_backend_api.time.sleep"):
            result = backend._wait_search_result("c1", timeout_secs=30, poll_interval_secs=0)

        self.assertEqual(result["answer"], FIRST_HALF + "\n\n" + SECOND_HALF)
        self.assertEqual(calls["count"], 4)


if __name__ == "__main__":
    unittest.main()
