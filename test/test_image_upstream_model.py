from __future__ import annotations

import unittest
from unittest import mock

from services.config import config
from services.model_service import model_catalog_service
from services.openai_backend_api import OpenAIBackendAPI


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


if __name__ == "__main__":
    unittest.main()
