from __future__ import annotations

import unittest
from unittest import mock

from services.config import config
from services.model_service import model_catalog_service
from services.openai_backend_api import CODEX_IMAGE_TOOL_MODELS, OpenAIBackendAPI
from utils.helper import UpstreamHTTPError


class ImageUpstreamModelTests(unittest.TestCase):
    def settings(self, configured: str, listed: bool | None, effort: str = "max") -> tuple[str, str]:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        with (
            mock.patch.dict(config.data, {"default_upstream_model_name": configured, "default_thinking_effort": effort}),
            mock.patch.object(model_catalog_service, "lists_model", return_value=listed),
        ):
            return backend._image_model_settings("gpt-image-2")

    def test_listed_model_is_used_with_its_effort(self) -> None:
        self.assertEqual(self.settings("gpt-6", True), ("gpt-6", "max"))
        self.assertEqual(self.settings("gpt-6-extended", True), ("gpt-6", "extended"))

    def test_model_gone_from_catalog_falls_back_to_auto(self) -> None:
        self.assertEqual(self.settings("gpt-5-5", False), ("auto", ""))

    def test_unreadable_catalog_keeps_the_setting(self) -> None:
        self.assertEqual(self.settings("gpt-5-5", None), ("gpt-5-5", "max"))


class CodexImageModelTests(unittest.TestCase):
    def run_with(self, outcomes: dict[str, object]) -> tuple[list[str], list[object]]:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        backend.access_token = "codex-token"
        tried: list[str] = []

        def request(_prompt, _images, _size, _quality, tool_model):
            tried.append(tool_model)
            outcome = outcomes[tool_model]
            if isinstance(outcome, Exception):
                raise outcome
            yield from outcome

        backend._codex_image_request = request
        with mock.patch.object(OpenAIBackendAPI, "_ensure_codex_source_account", lambda self: None):
            events = list(backend.iter_codex_image_response_events("a cube"))
        return tried, events

    def test_newest_model_is_asked_first(self) -> None:
        tried, events = self.run_with({CODEX_IMAGE_TOOL_MODELS[0]: [{"type": "done"}]})
        self.assertEqual((tried, events), ([CODEX_IMAGE_TOOL_MODELS[0]], [{"type": "done"}]))

    def test_rejected_name_falls_back_to_the_older_one(self) -> None:
        tried, events = self.run_with({
            "gpt-image-2.6-sunburst": UpstreamHTTPError("/codex", 400, {"detail": "unknown model"}),
            "gpt-image-2": [{"type": "done"}],
        })
        self.assertEqual(tried, ["gpt-image-2.6-sunburst", "gpt-image-2"])
        self.assertEqual(events, [{"type": "done"}])

    def test_other_errors_are_not_retried(self) -> None:
        with self.assertRaises(UpstreamHTTPError):
            self.run_with({"gpt-image-2.6-sunburst": UpstreamHTTPError("/codex", 429, "slow down")})


if __name__ == "__main__":
    unittest.main()
