from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.checkers.no_redundant_destructure import check


def violations_for(source: str) -> list:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        (src / "fixture.rs").write_text(source)
        return check(root)


class NoRedundantDestructureTest(unittest.TestCase):
    def test_rejects_immediately_destructured_call_result(self) -> None:
        found = violations_for(
            "fn import() {\n"
            "    let page_import_results = import_pages().await?;\n"
            "    let (pages, page_count, unit_count) = page_import_results;\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "DSTR001")
        self.assertEqual(found[0].line, 2)
        self.assertIn("bind `(pages, page_count, unit_count)` directly", found[0].message)

    def test_rejects_struct_slice_and_tuple_struct_patterns(self) -> None:
        found = violations_for(
            "fn unpack() {\n"
            "    let point = make_point();\n"
            "    let Point { x, y } = point;\n"
            "    let values = make_values();\n"
            "    let [first, second] = values;\n"
            "    let color = make_color();\n"
            "    let Color(red, green, blue) = color;\n"
            "}\n"
        )

        self.assertEqual(len(found), 3)

    def test_allows_direct_destructuring(self) -> None:
        found = violations_for(
            "fn unpack() {\n"
            "    let (pages, page_count, unit_count) = import_pages().await?;\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_allows_intermediate_use(self) -> None:
        found = violations_for(
            "fn unpack() {\n"
            "    let values = make_values();\n"
            "    inspect(&values);\n"
            "    let (first, second) = values;\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_allows_comment_between_bindings(self) -> None:
        found = violations_for(
            "fn unpack() {\n"
            "    let values = make_values();\n"
            "    // Keep this named for the debugger.\n"
            "    let (first, second) = values;\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_allows_non_destructuring_second_binding(self) -> None:
        found = violations_for(
            "fn unpack() {\n"
            "    let values = make_values();\n"
            "    let copied = values;\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_allows_destructuring_a_derived_value(self) -> None:
        found = violations_for(
            "fn unpack() {\n"
            "    let response = request();\n"
            "    let (head, body) = response.into_parts();\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_allows_let_else_that_can_use_temporary(self) -> None:
        found = violations_for(
            "fn unpack() {\n"
            "    let response = request();\n"
            "    let Some(body) = response else { return preserve(response); };\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_allows_plain_reference_binding(self) -> None:
        found = violations_for(
            "fn borrow() {\n"
            "    let value = make_value();\n"
            "    let ref borrowed = value;\n"
            "}\n"
        )

        self.assertEqual(found, [])
