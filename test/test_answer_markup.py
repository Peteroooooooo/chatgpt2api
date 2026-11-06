from __future__ import annotations

import unittest

from services.openai_backend_api import OpenAIBackendAPI
from utils.answer_markup import clean_answer_markup


class CleanAnswerMarkupTests(unittest.TestCase):
    def test_entity_becomes_its_value(self) -> None:
        text = 'Try <Entity value="RustDesk" category="software"/> or <Entity category="x" value=\'AnyDesk &amp; co\' />.'

        self.assertEqual(clean_answer_markup(text), "Try RustDesk or AnyDesk & co.")

    def test_cite_is_removed_with_space_before_it(self) -> None:
        text = 'It is open source <Cite ref="turn325659search2"/>. Next <Cite ref="a"/><Cite ref="b"/> line.'

        self.assertEqual(clean_answer_markup(text), "It is open source. Next line.")

    def test_layout_tags_removed_and_inner_text_kept(self) -> None:
        text = (
            "Intro\n\n"
            '<box variant="card"\n     gap={3}\n     style={{padding: 2}}>\n'
            '<row align="center">\n'
            '<title level={2}>Options</title>\n'
            '<badge tone="info">New</badge> <text weight="bold">RustDesk</text>\n'
            "</row>\n"
            "<col/>\n"
            "</box>\n\n\n"
            "Outro"
        )

        self.assertEqual(clean_answer_markup(text), "Intro\n\nOptions\nNew RustDesk\n\nOutro")

    def test_fenced_code_is_untouched(self) -> None:
        code = '```html\n<title>Page</title>\n<Cite ref="x"/>\n<box>\n\n\n</box>\n```'
        text = f'<title>Head</title>\n\n{code}\n\nAfter <Cite ref="y"/>.'

        self.assertEqual(clean_answer_markup(text), f"Head\n\n{code}\n\nAfter.")

    def test_unclosed_fence_protects_rest(self) -> None:
        text = "Before <badge>x</badge>\n```\n<box>keep</box>"

        self.assertEqual(clean_answer_markup(text), "Before x\n```\n<box>keep</box>")

    def test_inline_code_is_untouched(self) -> None:
        text = "Use `<title>` in HTML, see <Cite ref=\"t\"/>."

        self.assertEqual(clean_answer_markup(text), "Use `<title>` in HTML, see.")

    def test_other_html_like_text_is_kept(self) -> None:
        text = "a <b>bold</b>, <textarea> and <Boxed> plus x<y and <titles>\n\n\n\nkept"

        self.assertEqual(clean_answer_markup(text), text)

    def test_plain_text_unchanged(self) -> None:
        self.assertEqual(clean_answer_markup("no tags\n\n\n\nhere"), "no tags\n\n\n\nhere")
        self.assertEqual(clean_answer_markup(""), "")

    def test_link_becomes_markdown_link_or_its_title(self) -> None:
        text = 'See <Link ref="turn1search2" title="Vite on npm"/> and <Link ref="turn1search9" title="Docs"/>.'
        urls = {"turn1search2": "https://www.npmjs.com/package/vite"}

        self.assertEqual(
            clean_answer_markup(text, lambda ref: urls.get(ref, "")),
            "See [Vite on npm](https://www.npmjs.com/package/vite) and Docs.",
        )

    def test_widgets_and_dividers_are_removed(self) -> None:
        text = 'A\n<AsyncImage query="logo" aspectRatio="1:1" maxWidth="104px"/>\n<divider color="subtle"/>\nB <SuggestAutomation label="Track" emoji="x"/>'

        self.assertEqual(clean_answer_markup(text), "A\n\nB")

    def test_generic_type_names_are_kept(self) -> None:
        text = "Use List<String> or Map<K, V> here, and <Foo/> stays."

        self.assertEqual(clean_answer_markup(text), text)

    def test_text_inside_layout_loses_layout_indent(self) -> None:
        text = '<box gap={1}>\n    **Title**\n    - item\n</box>\n    indented code stays'

        self.assertEqual(clean_answer_markup(text), "\n**Title**\n- item\n\n    indented code stays")


class SearchAnswerMarkupTests(unittest.TestCase):
    def test_search_answer_is_cleaned(self) -> None:
        message = {
            "id": "f1",
            "author": {"role": "assistant"},
            "channel": "final",
            "create_time": 2.0,
            "content": {"content_type": "text", "parts": [
                '<title>Remote tools</title>\n\n<Entity value="RustDesk" category="software"/> works <Cite ref="turn1search2"/>.'
            ]},
            "metadata": {},
        }
        conversation = {
            "current_node": "f1",
            "mapping": {
                "u1": {"message": {"author": {"role": "user"}, "content": {"parts": ["q"]}}, "parent": None},
                "f1": {"message": message, "parent": "u1"},
            },
        }
        result = object.__new__(OpenAIBackendAPI)._extract_search_result("c1", conversation)

        self.assertEqual(result["answer"], "Remote tools\n\nRustDesk works.")


if __name__ == "__main__":
    unittest.main()
