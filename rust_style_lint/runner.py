"""Read a project's rust-style-lint.toml and dispatch the enabled checkers."""

from __future__ import annotations

import argparse
import importlib
import sys
import tomllib
from pathlib import Path

from .base import Violation

CONFIG_FILENAME = "rust-style-lint.toml"

CHECKER_NAMES = (
    "no-allow",
    "no-inline-format",
    "no-inline-tests",
    "spacing-style",
    "use-style",
    "generic-where",
    "no-type-hint",
    "item-layout",
    "doc-comment-coverage",
    "forbidden-identifiers",
    "module-dependency",
    "visibility-style",
    "trait-use-anonymous",
    "prefer-if-let-guard",
    "no-unnamed-fields",
)

DEFAULT_SECTIONS: dict[str, dict] = {}


def module_name(name: str) -> str:
    return name.replace("-", "_")


def load_config(root: Path) -> dict:
    path = root / CONFIG_FILENAME

    if not path.is_file():
        return {}

    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_checker(name: str):
    module = importlib.import_module(f"rust_style_lint.checkers.{module_name(name)}")
    return module


def run_checker(module, root: Path, section: dict | None, fix: bool) -> list[Violation]:
    if fix and hasattr(module, "fix"):
        return module.fix(root, section)

    return module.check(root, section)


def enabled_linters(config: dict) -> list[str]:
    linters = config.get("linters", {})

    return [name for name in CHECKER_NAMES if linters.get(name) is True]


def run_self_tests() -> int:
    failed = False

    for name in CHECKER_NAMES:
        module = load_checker(name)

        if not hasattr(module, "self_test"):
            continue

        print(f"━━━ self-test: {name} ━━━")
        result = module.self_test()

        if result == 0:
            print(f"✓ {name} passed")
        else:
            print(f"✗ {name} failed")
            failed = True

    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="rust-style-lint",
        description="Run Rust style checkers selected by rust-style-lint.toml",
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()

    if args.self_test:
        return run_self_tests()

    config = load_config(root)
    names = enabled_linters(config)

    if not names:
        print(
            f"warning: no linters enabled in {root / CONFIG_FILENAME}; "
            "add a [linters] table with `name = true` entries",
            file=sys.stderr,
        )

    failed = False

    for name in names:
        module = load_checker(name)
        section = config.get(name)
        violations = run_checker(module, root, section, args.fix)

        for violation in violations:
            location = (
                f"{violation.path}:{violation.line}:{violation.column}"
                if violation.column is not None
                else f"{violation.path}:{violation.line}"
            )
            prefix = "warning" if violation.level == "warning" else "error"

            if violation.level != "warning":
                failed = True

            print(
                f"{location}: {violation.code}: {prefix}: {violation.message}",
                file=sys.stderr,
            )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
