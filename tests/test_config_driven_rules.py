from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.checkers.forbidden_identifiers import check as check_identifiers
from rust_style_lint.checkers.no_inline_format import check as check_inline_format
from rust_style_lint.checkers.prefer_if_let_guard import check as check_match


def project_with(source: str) -> tuple[tempfile.TemporaryDirectory, Path]:
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    src = root / "src"
    src.mkdir()
    (src / "fixture.rs").write_text(source)

    return directory, root


class ConfigDrivenRulesTest(unittest.TestCase):
    def test_empty_identifier_section_enables_no_hidden_rules(self) -> None:
        directory, root = project_with(
            "fn target_error(err: SomeError) { let error = err; }\n",
        )

        with directory:
            self.assertEqual(check_identifiers(root, {}), [])

    def test_identifier_code_replacement_and_context_come_from_config(self) -> None:
        directory, root = project_with("fn custom_bad() {}\nstruct Bad;\n")
        config = {
            "words": [
                {
                    "word": "bad",
                    "code": "CUS001",
                    "replacement": "good",
                    "contexts": ["function"],
                },
            ],
        }

        with directory:
            found = check_identifiers(root, config)

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "CUS001")
        self.assertEqual(
            found[0].message,
            "'custom_bad' uses 'bad'; use 'good' instead",
        )

    def test_empty_replacement_means_word_is_forbidden(self) -> None:
        directory, root = project_with("fn custom_bad() {}\n")
        config = {
            "words": [
                {
                    "word": "bad",
                    "code": "CUS001",
                    "replacement": "",
                    "contexts": ["function"],
                },
            ],
        }

        with directory:
            found = check_identifiers(root, config)

        self.assertEqual(found[0].message, "'custom_bad' contains forbidden word 'bad'")

    def test_global_and_per_word_module_exemptions_include_descendants(self) -> None:
        directory, root = project_with(
            "fn bad_root() {}\n"
            "mod allowed { fn bad_here() {} mod nested { fn bad_nested() {} } }\n"
            "mod per_rule { fn bad_here() {} }\n",
        )
        config = {
            "allowed_modules": ["crate::fixture::allowed"],
            "words": [
                {
                    "word": "bad",
                    "code": "CUS001",
                    "replacement": "good",
                    "contexts": ["function"],
                    "allowed_modules": ["crate::fixture::per_rule"],
                },
            ],
        }

        with directory:
            found = check_identifiers(root, config)

        self.assertEqual([violation.message for violation in found], [
            "'bad_root' uses 'bad'; use 'good' instead",
        ])

    def test_default_replacements_and_current_abbreviations(self) -> None:
        directory, root = project_with(
            "fn f() { let replacements = 1; let current = 2; let message = 3; }\n",
        )

        with directory:
            found = check_identifiers(root)

        self.assertEqual(
            {(violation.code, violation.message) for violation in found},
            {
                ("FBD012", "'replacements' uses 'replacements'; use 'repl' instead"),
                ("FBD013", "'current' uses 'current'; use 'curr' instead"),
            },
        )

    def test_inline_format_message_uses_configured_macro_name(self) -> None:
        directory, root = project_with('fn f() { custom_format!("{value}"); }\n')

        with directory:
            found = check_inline_format(root, {"macros": ["custom_format"]})

        self.assertEqual(len(found), 1)
        self.assertIn("custom_format!", found[0].message)
        self.assertNotIn("format!(\"", found[0].message)

    def test_configured_writer_macro_uses_second_argument(self) -> None:
        directory, root = project_with(
            'fn f(out: &mut String) { custom_write!(out, "{value}"); }\n',
        )

        with directory:
            found = check_inline_format(
                root,
                {"writer_macros": ["custom_write"]},
            )

        self.assertEqual(len(found), 1)
        self.assertIn("custom_write!", found[0].message)

    def test_missing_replacement_keys_do_not_restore_code_defaults(self) -> None:
        directory, root = project_with(
            "pub fn f(value: Option<u8>) {\n"
            "    match value { Some(x) => foo(x), None => panic!() }\n"
            "}\n",
        )

        with directory:
            self.assertEqual(check_inline_format(root, {}), [])
            self.assertEqual(check_match(root, {}), [])


if __name__ == "__main__":
    unittest.main()
