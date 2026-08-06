from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.production_source import production_source


class ProductionSourceTest(unittest.TestCase):
    def test_masks_inline_test_only_cfg_combinations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            source = src / "lib.rs"
            source.write_text(
                "#[cfg(all(test, feature = \"rdb\"))]\n"
                "mod integration { fn only_test() {} }\n"
                "#[cfg(any(test, feature = \"rdb\"))]\n"
                "mod optional { fn maybe_production() {} }\n"
                "#[cfg(not(test))]\n"
                "mod production { fn always_production() {} }\n"
            )

            visible = production_source(source, root)

            self.assertNotIn(b"only_test", visible)
            self.assertIn(b"maybe_production", visible)
            self.assertIn(b"always_production", visible)

    def test_masks_external_test_only_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            parent = src / "feature.rs"
            child = src / "feature" / "integration.rs"
            child.parent.mkdir()
            parent.write_text("#[cfg(all(test, feature = \"rdb\"))]\nmod integration;\n")
            child.write_text("pub fn only_test() {}\n")

            visible = production_source(child, root)

            self.assertNotIn(b"only_test", visible)
            self.assertEqual(visible.count(b"\n"), 1)


if __name__ == "__main__":
    unittest.main()
