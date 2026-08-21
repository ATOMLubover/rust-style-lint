from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.checkers.prefer_if_let_guard import check


def violations_for(source: str) -> list:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        (src / "fixture.rs").write_text(source)
        return check(root)


class PreferIfLetGuardTest(unittest.TestCase):
    def test_diverging_guard_suggests_let_else(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => return,\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("let Some(x) = value else", found[0].message)
        self.assertEqual(found[0].line, 2)

    def test_empty_guard_suggests_if_let(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => {},\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("if let Some(x) = value", found[0].message)

    def test_unit_guard_counts_as_empty(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        _ => (),\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("if let Some(x) = value", found[0].message)

    def test_false_business_arm_suggests_negated_if(self) -> None:
        found = violations_for(
            "pub async fn f(created: bool) {\n"
            "    match created {\n"
            "        //\n"
            "        true => {}\n"
            "\n"
            "        false => {\n"
            "            //\n"
            "            update().await;\n"
            "        }\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("if !created", found[0].message)
        self.assertNotIn("if let", found[0].message)

    def test_true_business_arm_suggests_plain_if(self) -> None:
        found = violations_for(
            "pub fn f(created: bool) {\n"
            "    match created {\n"
            "        true => update(),\n"
            "        false => {},\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("if created", found[0].message)
        self.assertNotIn("if let", found[0].message)

    def test_negated_compound_boolean_keeps_precedence(self) -> None:
        found = violations_for(
            "pub fn f(a: bool, b: bool) {\n"
            "    match a && b {\n"
            "        true => {},\n"
            "        false => update(),\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("if !(a && b)", found[0].message)

    def test_comment_separators_are_not_arms(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    let ok = match value {\n"
            "        //\n"
            "        // Internal implementation detail.\n"
            "        Some(x) => x,\n"
            "\n"
            "        None => return,\n"
            "    };\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("let Some(x) = value else", found[0].message)

    def test_break_guard_in_loop(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    loop {\n"
            "        match value {\n"
            "            Some(x) => foo(x),\n"
            "            None => break,\n"
            "        }\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("let Some(x) = value else", found[0].message)

    def test_diverging_block_with_leading_statements(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => {\n"
            "            warn(\"missing\");\n"
            "            return;\n"
            "        },\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertIn("let Some(x) = value else", found[0].message)

    def test_macro_guard_diverges(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => unreachable!(),\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)

    def test_identical_multi_guard_collapses(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => return,\n"
            "        _ => return,\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)

    def test_two_business_arms_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => bar(),\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_multi_pattern_dispatch_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => a(x),\n"
            "        Some(y) => b(y),\n"
            "        None => return,\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_wildcard_business_fallback_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        _ => default(),\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_match_guard_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) if x > 5 => foo(x),\n"
            "        None => return,\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_guard_body_using_pattern_binding_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Result<u8, String>) -> u8 {\n"
            "    match value {\n"
            "        Ok(x) => x,\n"
            "        Err(err) => return err.len() as u8,\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_wildcard_binding_guard_still_flagged(self) -> None:
        found = violations_for(
            "pub fn f(value: Result<u8, String>) -> u8 {\n"
            "    match value {\n"
            "        Ok(x) => x,\n"
            "        Err(_) => return 0,\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(len(found), 1)

    def test_mixed_empty_and_diverging_guards_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => {},\n"
            "        _ => return,\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_distinct_diverging_guards_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => return,\n"
            "        _ => panic!(\"impossible\"),\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_conditional_guard_block_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => {\n"
            "            if x {\n"
            "                return;\n"
            "            }\n"
            "        },\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_single_arm_kept(self) -> None:
        found = violations_for(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "    }\n"
            "}\n"
        )

        self.assertEqual(found, [])

    def test_custom_diverging_macro_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (src / "fixture.rs").write_text(
                "pub fn f(value: Option<u8>) {\n"
                "    match value {\n"
                "        Some(x) => foo(x),\n"
                "        None => bail!(),\n"
                "    }\n"
                "}\n"
            )

            found = check(root)
            self.assertEqual(found, [])

            found = check(root, {"diverging_macros": ["bail"]})
            self.assertEqual(len(found), 1)
