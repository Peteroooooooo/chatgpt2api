from __future__ import annotations

import random
import re
import unittest

from services.real_chat_prompt_bank import BANKS, SMALL, TEMPLATES, fill_template, template_slots
from services.real_chat_test_prompts import (
    REAL_CHAT_TEST_PROMPTS,
    choose_real_chat_test_prompt,
    generate_real_chat_test_prompt,
)

# Words that would push ChatGPT into image generation or web search.
FORBIDDEN = ("画", "图片", "照片生成", "最新", "新闻", "draw", "image", "picture", "latest", "news", "today")


def _bank_words(lang: str) -> list[str]:
    words: list[str] = []
    for bank in BANKS[lang].values():
        for item in bank:
            words.extend(item if isinstance(item, tuple) else (item,))
    for bank in SMALL[lang].values():
        words.extend(bank)
    return words


class PromptBankTests(unittest.TestCase):
    def test_every_template_slot_has_a_bank(self) -> None:
        for lang, templates in TEMPLATES.items():
            for template in templates:
                for slot in template_slots(template):
                    if slot in ("pair_a", "pair_b"):
                        self.assertIn("pair", BANKS[lang])
                    else:
                        self.assertTrue(slot in BANKS[lang] or slot in SMALL[lang], f"{template.key}: {slot}")

    def test_pairs_have_two_sides(self) -> None:
        for lang in BANKS:
            for pair in BANKS[lang]["pair"]:
                self.assertEqual(len(pair), 2, pair)
                self.assertTrue(all(side.strip() for side in pair), pair)

    def test_banks_and_templates_avoid_image_and_search_words(self) -> None:
        for lang in BANKS:
            texts = _bank_words(lang) + [template.text for template in TEMPLATES[lang]]
            for text in texts:
                lowered = text.lower()
                for word in FORBIDDEN:
                    self.assertNotIn(word, lowered, text)

    def test_filled_prompts_have_no_leftover_slots(self) -> None:
        rng = random.Random(1)
        for templates in TEMPLATES.values():
            for template in templates:
                for _ in range(20):
                    text = fill_template(template, rng)
                    self.assertIsNone(re.search(r"[{}]", text), text)
                    self.assertLess(len(text), 160, text)


class PromptChooserTests(unittest.TestCase):
    def test_generated_ids_name_the_template(self) -> None:
        prompt = generate_real_chat_test_prompt("en", rng=random.Random(2))

        self.assertRegex(prompt.id, r"^gen:en-[a-z-]+:[0-9a-f]{8}$")

    def test_generator_skips_recently_used_templates(self) -> None:
        templates = TEMPLATES["zh"]
        recent = [f"gen:{template.key}:00000000" for template in templates[:-1]]

        for seed in range(10):
            prompt = generate_real_chat_test_prompt("zh", recent, random.Random(seed))
            self.assertTrue(prompt.id.startswith(f"gen:{templates[-1].key}:"), prompt.id)

    def test_generator_falls_back_when_every_template_is_recent(self) -> None:
        recent = [f"gen:{template.key}:00000000" for template in TEMPLATES["en"]]

        prompt = generate_real_chat_test_prompt("en", recent, random.Random(3))

        self.assertTrue(prompt.id.startswith("gen:en-"))

    def test_chooser_mixes_all_three_sources(self) -> None:
        rng = random.Random(4)
        fixed_ids = {item.id for item in REAL_CHAT_TEST_PROMPTS}
        counts = {"fixed": 0, "zh": 0, "en": 0}
        texts: set[str] = set()
        for _ in range(1000):
            prompt = choose_real_chat_test_prompt((), rng)
            texts.add(prompt.text)
            if prompt.id in fixed_ids:
                counts["fixed"] += 1
            else:
                counts[prompt.id.split(":")[1].split("-")[0]] += 1

        self.assertTrue(all(count > 100 for count in counts.values()), counts)
        self.assertGreater(len(texts), 700)


if __name__ == "__main__":
    unittest.main()
