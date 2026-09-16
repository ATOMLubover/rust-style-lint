from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.checkers.doc_comment_coverage import check


def violations_for(source: str) -> list:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        (src / "fixture.rs").write_text(source)
        return check(root)


class DocCommentCoverageTest(unittest.TestCase):
    def test_reports_first_attribute_for_public_and_private_items(self) -> None:
        for declaration in (
            "pub fn target() {}",
            "fn target() {}",
            "pub struct Target;",
            "struct Target;",
        ):
            with self.subTest(declaration=declaration):
                wrong_comment = "//" if declaration.startswith("pub ") else "///"
                found = violations_for(
                    "\n#[allow(dead_code)]\n"
                    f"{wrong_comment} Wrong comment style.\n"
                    "#[custom::attribute(\n    option = true\n)]\n"
                    "#[derive(Debug)]\n"
                    f"{declaration}\n"
                )
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].code, "DOC001")
                self.assertEqual(found[0].line, 2)

    def test_reports_start_of_multiline_attribute(self) -> None:
        found = violations_for(
            "\n#[derive(\n    Debug,\n    Clone,\n)]\npub struct Target;\n"
        )
        self.assertEqual([item.line for item in found], [2])

    def test_reports_declaration_start_without_attributes(self) -> None:
        found = violations_for("\npub\nasync fn\ntarget() {}\n")
        self.assertEqual([item.line for item in found], [2])

    def test_nested_members_report_their_own_attributes(self) -> None:
        cases = (
            ("/// Type.\npub struct Target {\n", "pub field: u32,"),
            ("/// Type.\npub enum Target {\n", "Variant,"),
            ("/// Type.\npub trait Target {\n", "fn method(&self);"),
            ("/// Type.\npub struct Target;\nimpl Target {\n", "pub fn method(&self) {}"),
        )
        for prefix, member in cases:
            with self.subTest(member=member):
                found = violations_for(
                    prefix + "    #[allow(dead_code)]\n"
                    "    #[custom::attribute]\n"
                    f"    {member}\n}}\n"
                )
                self.assertEqual([item.line for item in found], [prefix.count("\n") + 1])

    def test_does_not_reuse_previous_declarations_attributes(self) -> None:
        found = violations_for(
            "/// Documented.\n#[derive(Debug)]\npub struct First;\n"
            "\npub struct Second;\n"
        )
        self.assertEqual([item.line for item in found], [5])

    def test_comments_above_attributes_satisfy_coverage(self) -> None:
        for comment, declaration in (
            ("/// Documented.", "pub struct Target;"),
            ("// Documented.", "struct Target;"),
            ("/** Documented. */", "pub fn target() {}"),
            ("/* Documented. */", "fn target() {}"),
        ):
            with self.subTest(comment=comment):
                self.assertEqual(
                    violations_for(
                        f"{comment}\n#[allow(dead_code)]\n"
                        f"#[custom::attribute]\n{declaration}\n"
                    ),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
