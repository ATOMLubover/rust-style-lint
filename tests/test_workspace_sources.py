from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.base import crate_roots, source_files, source_root
from rust_style_lint.checkers.module_dependency import check as check_module_dependency
from rust_style_lint.checkers.no_inline_format import check as check_inline_format
from rust_style_lint.checkers.visibility_style import check as check_visibility
from rust_style_lint.production_source import production_source


class WorkspaceSourcesTest(unittest.TestCase):
    def test_discovers_every_crate_src_and_excludes_test_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = {
                root / "src" / "lib.rs",
                root / "demo-macro" / "src" / "lib.rs",
                root / "demo-util" / "src" / "nested.rs",
            }

            for path in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("pub fn production() {}\n")

            tests = root / "demo-util" / "src" / "tests.rs"
            nested_tests = root / "demo-util" / "src" / "tests" / "fixture.rs"
            target = root / "target" / "debug" / "build" / "generated" / "src" / "lib.rs"

            for path in (tests, nested_tests, target):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fn test_only() {}\n")

            self.assertEqual(set(source_files(root)), expected)
            self.assertEqual(
                set(crate_roots(root)),
                {root, root / "demo-macro", root / "demo-util"},
            )
            self.assertEqual(
                source_root(root / "demo-util" / "src" / "nested.rs", root),
                root / "demo-util" / "src",
            )

    def test_checker_reports_nested_workspace_crate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "demo-macro" / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text('pub fn render(value: u8) { println!("{value}"); }\n')

            violations = check_inline_format(root)

            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].path, Path("demo-macro/src/lib.rs"))

    def test_nested_crate_test_module_is_masked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "demo-util" / "src" / "lib.rs"
            tests = root / "demo-util" / "src" / "checks.rs"
            source.parent.mkdir(parents=True)
            source.write_text("#[cfg(test)]\nmod checks;\n")
            tests.write_text("fn test_only() {}\n")

            self.assertNotIn(b"test_only", production_source(tests, root))

    def test_module_graphs_are_checked_per_workspace_crate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            crate = root / "demo-util"
            parent = crate / "src" / "parent.rs"
            child = crate / "src" / "parent" / "child.rs"
            child.parent.mkdir(parents=True)
            (crate / "Cargo.toml").write_text('[package]\nname = "demo-util"\n')
            (crate / "src" / "lib.rs").write_text("mod parent;\n")
            parent.write_text("pub mod child;\npub struct Parent;\n")
            child.write_text("use super::Parent;\n")

            violations = check_module_dependency(root)

            self.assertIn("MOD001", {violation.code for violation in violations})
            self.assertIn(
                Path("demo-util/src/parent/child.rs"),
                {violation.path for violation in violations},
            )

    def test_visibility_uses_nested_crate_module_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "demo-util" / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text("pub(crate) struct Internal;\n")

            violations = check_visibility(root)

            self.assertEqual([violation.code for violation in violations], ["VIS001"])
            self.assertEqual(violations[0].path, Path("demo-util/src/lib.rs"))


if __name__ == "__main__":
    unittest.main()
