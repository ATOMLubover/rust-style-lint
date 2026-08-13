"""Forbid any lint-suppression attribute: `#[allow(...)]` / `#[expect(...)]`.

Suppressions hide real problems and rot as the code changes. The only way to
satisfy this rule is to restructure the code so the lint stops firing:
conditionally compile an import (`#[cfg(feature = "swagger")]`), drop dead
code, make an item reachable, split a function, and so on. Non-suppression
attributes (`#[cfg(...)]`, `#[derive(...)]`, `#[cfg_attr(..., derive(...))]`,
`#[deprecated]`, ...) are untouched.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))
SUPPRESSION = re.compile(r"^\s*#\s*\[\s*(?:allow|expect)\s*\(([^]]*)\)\s*\]", re.DOTALL)


def text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def descendants(node: tree_sitter.Node, kind: str) -> list[tree_sitter.Node]:
    found: list[tree_sitter.Node] = []
    pending = [node]

    while pending:
        current = pending.pop()

        if current.type == kind:
            found.append(current)

        pending.extend(reversed(current.named_children))

    return found


def check_file(path: Path, root: Path, source: bytes) -> list[Violation]:
    tree = PARSER.parse(source)
    violations: list[Violation] = []

    for attribute in descendants(tree.root_node, "attribute_item"):
        match = SUPPRESSION.match(text(source, attribute))

        if match is None:
            continue

        lints = " ".join(match.group(1).split())

        violations.append(
            Violation(
                path=path.relative_to(root),
                line=source.count(b"\n", 0, attribute.start_byte) + 1,
                code="NO_ALLOW",
                message=(
                    f"`#[allow({lints})]` is forbidden — restructure the "
                    "code to eliminate the lint instead of silencing it"
                ),
            ),
        )

    return violations


def rust_files(root: Path, paths: list[Path]) -> list[Path]:
    if paths:
        files: list[Path] = []

        for path in paths:
            resolved = path.resolve()

            if resolved.is_file() and resolved.suffix == ".rs":
                files.append(resolved)
            elif resolved.is_dir():
                files.extend(resolved.rglob("*.rs"))

        return sorted(set(files))

    return sorted((root / "src").rglob("*.rs"))


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return violations for every allow/expect attribute."""
    violations: list[Violation] = []

    for path in rust_files(root, []):
        violations.extend(check_file(path, root, path.read_bytes()))

    return violations


def fix(root: Path, config: dict | None = None) -> list[Violation]:
    """Auto-fix is a no-op: the suppression must be removed by restructuring."""
    return check(root, config)


def main() -> int:
    parser = argparse.ArgumentParser(prog="no-allow", description="Forbid #[allow(...)] / #[expect(...)] attributes")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--fix", action="store_true", help="accepted for compatibility; auto-fix is not implemented")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = args.root.resolve()
    violations: list[Violation] = []

    for path in rust_files(root, args.paths):
        violations.extend(check_file(path, root, path.read_bytes()))

    if args.fix:
        print("note: --fix is a no-op; remove the suppression by restructuring", file=sys.stderr)

    for violation in violations:
        print(f"{violation.path}:{violation.line}: {violation.code}: {violation.message}", file=sys.stderr)

    return 1 if violations else 0


def self_test() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir(parents=True)
        fixture = src / "lib.rs"

        fixture.write_text(
            "#[allow(unused_imports)]\n"
            "#[expect(clippy::too_many_arguments)]\n"
            "pub fn f() {}\n",
        )
        violations = check(root)

        if len(violations) != 2 or any(violation.code != "NO_ALLOW" for violation in violations):
            print("self-test: allow/expect were not diagnosed", file=sys.stderr)
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

        fixture.write_text(
            "#[cfg(feature = \"x\")]\n"
            "#[derive(Default)]\n"
            "#[cfg_attr(test, derive(Clone))]\n"
            "fn f() {}\n",
        )
        violations = check(root)

        if violations:
            print("self-test: non-suppression attributes were flagged", file=sys.stderr)
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
