from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rust_style_lint import runner
from rust_style_lint.base import Violation, print_rule_guidance


class RuleGuidanceTest(unittest.TestCase):
    def test_every_rule_has_a_readable_document(self) -> None:
        for name in runner.CHECKER_NAMES:
            with self.subTest(name=name):
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    print_rule_guidance(name, [Violation(Path("src/lib.rs"), 1, "CUSTOM", "bad")])
                message = output.getvalue()
                document = Path(message.split(": ", 1)[1].splitlines()[0])
                self.assertTrue(document.is_file())
                self.assertEqual(document.name, f"{name}.md")
                self.assertIn("you must read the rule documentation", message)
                self.assertIn("Do not inspect code before reading this document", message)
                self.assertIn("Do not diagnose or modify code based only on error output", message)

    def test_runner_and_standalone_require_docs_only_for_violations(self) -> None:
        module = runner.load_checker("doc-comment-coverage")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            fixture = root / "src/lib.rs"
            (root / "rust-style-lint.toml").write_text(
                '[linters]\ndoc-comment-coverage = true\n'
            )
            for main in (runner.main, module.main):
                for valid in (False, True):
                    with self.subTest(main=main.__module__, valid=valid):
                        fixture.write_text(
                            "/// First.\npub struct First;\n/// Second.\npub struct Second;\n"
                            if valid else "pub struct First;\npub struct Second;\n"
                        )
                        output = io.StringIO()
                        with patch("sys.argv", ["lint", "--root", str(root)]):
                            with contextlib.redirect_stderr(output):
                                result = main()
                        self.assertEqual(result, 0 if valid else 1)
                        message = output.getvalue()
                        self.assertEqual(message.count("you must read"), 0 if valid else 1)
                        if not valid:
                            self.assertIn("doc-comment-coverage.md", message)
                            self.assertEqual(message.count("DOC001"), 2)


if __name__ == "__main__":
    unittest.main()
