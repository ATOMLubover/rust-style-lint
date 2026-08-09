from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.checkers.no_unnamed_fields import check


def violations_for(source: str) -> list:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        (src / "fixture.rs").write_text(source)
        return check(root)


class NoUnnamedFieldsTest(unittest.TestCase):
    def test_multi_field_tuple_flagged(self) -> None:
        found = violations_for(
            "pub enum Foo {\n"
            "    Variant(String, u32),\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "ENUM001")
        self.assertEqual(found[0].line, 2)
        self.assertIn("Variant", found[0].message)

    def test_single_field_tuple_flagged(self) -> None:
        found = violations_for(
            "pub enum Foo {\n"
            "    Variant(String),\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("Variant", found[0].message)

    def test_empty_tuple_flagged(self) -> None:
        found = violations_for(
            "pub enum Foo {\n"
            "    Variant(),\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("Variant", found[0].message)

    def test_named_fields_ok(self) -> None:
        found = violations_for(
            "pub enum Foo {\n"
            "    Variant { name: String, age: u32 },\n"
            "    EmptyNamed {},\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_unit_variants_ok(self) -> None:
        found = violations_for(
            "pub enum Foo {\n"
            "    A,\n"
            "    B = 5,\n"
            "    C = 1 << 4,\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_mixed_enum_only_tuple_flagged(self) -> None:
        found = violations_for(
            "pub enum Foo {\n"
            "    Unit,\n"
            "    Named { name: String },\n"
            "    Tuple(String),\n"
            "    Unit2,\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("Tuple", found[0].message)

    def test_multiple_tuple_variants_all_flagged(self) -> None:
        found = violations_for(
            "pub enum Foo {\n"
            "    A(u8),\n"
            "    B { ok: bool },\n"
            "    C(String, String),\n"
            "}\n"
        )

        self.assertEqual(len(found), 2)
        messages = [violation.message for violation in found]
        self.assertTrue(any("A" in message for message in messages))
        self.assertTrue(any("C" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
