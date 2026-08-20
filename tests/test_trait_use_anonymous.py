from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.checkers.trait_use_anonymous import check


def violations_for(source: str) -> list:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        (src / "fixture.rs").write_text(source)
        return check(root)


def configured_violations_for(source: str, config: dict) -> list:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        (src / "fixture.rs").write_text(source)
        return check(root, config)


class TraitUseAnonymousTest(unittest.TestCase):
    def test_external_method_resolution_import_is_detected_without_allowlist(self) -> None:
        found = violations_for(
            "use diesel_async::RunQueryDsl;\n"
            "fn load(query: Query, conn: &mut Conn) { query.load(conn); }\n"
        )

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "TRAIT001")
        self.assertIn("diesel_async::RunQueryDsl", found[0].message)

    def test_function_local_method_resolution_import_is_detected(self) -> None:
        found = violations_for(
            "fn load(query: Query, conn: &mut Conn) {\n"
            "    use diesel_async::RunQueryDsl;\n"
            "    query.load(conn);\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].line, 2)

    def test_explicit_use_before_import_is_not_reported(self) -> None:
        found = violations_for(
            "fn require<T: RunQueryDsl>() {}\n"
            "use diesel_async::RunQueryDsl;\n"
        )

        self.assertEqual(found, [])

    def test_explicit_alias_use_is_not_reported(self) -> None:
        found = violations_for(
            "use diesel_async::RunQueryDsl as QueryDsl;\n"
            "fn require<T: QueryDsl>() {}\n"
        )

        self.assertEqual(found, [])

    def test_anonymous_and_public_imports_are_not_reported(self) -> None:
        found = violations_for(
            "use diesel_async::RunQueryDsl as _;\n"
            "pub use diesel_async::AsyncConnection;\n"
        )

        self.assertEqual(found, [])

    def test_lowercase_function_import_is_not_treated_as_trait(self) -> None:
        found = violations_for("use crate::shared::result::diesel;\n")

        self.assertEqual(found, [])

    def test_configured_macro_invocation_exempts_generated_import_uses(self) -> None:
        found = configured_violations_for(
            "use crate::result::BaseRest;\n"
            "preloadable! { owner: Info }\n",
            {"macro_markers": ["preloadable!"]},
        )

        self.assertEqual(found, [])

    def test_macro_definition_does_not_exempt_method_trait_import(self) -> None:
        found = configured_violations_for(
            "use diesel_async::RunQueryDsl;\n"
            "macro_rules! preloadable { () => {} }\n"
            "fn load(query: Query, conn: &mut Conn) { query.load(conn); }\n",
            {"macro_markers": ["preloadable!"]},
        )

        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
