"""Forbid unnamed (tuple) fields in enum variants.

Every enum variant must carry named fields. Tuple-style variants are never
allowed — every field deserves a name that says what it is::

    enum Error {
        Model(String, u32),            // BAD — unnamed fields
        Empty(),                       // BAD — unnamed, even when empty
        Unit,                          // GOOD — no fields
        Model { message: String },     // GOOD — named fields
    }

A unit variant has no fields and stays; a struct variant already names every
field. Only the `Variant(..)` form is flagged.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation, source_files
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))


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


def rust_files(root: Path) -> list[Path]:
    return source_files(root)


def check_file(path: Path, root: Path, source: bytes) -> list[Violation]:
    tree = PARSER.parse(source)
    violations: list[Violation] = []

    for variant in descendants(tree.root_node, "enum_variant"):
        if any(child.type == "ordered_field_declaration_list" for child in variant.named_children):
            name = variant.child_by_field_name("name")
            name_text = text(source, name) if name is not None else "<unnamed>"
            violations.append(
                Violation(
                    path=path.relative_to(root),
                    line=variant.start_point.row + 1,
                    code="ENUM001",
                    message=(
                        f"enum variant `{name_text}` has unnamed fields; "
                        "give every field a name or use a unit variant"
                    ),
                ),
            )

    return violations


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return violations for enum variants with unnamed (tuple) fields."""
    violations: list[Violation] = []

    for path in rust_files(root):
        source = path.read_bytes()
        visible_source = production_source(path, root)
        violations.extend(check_file(path, root, visible_source))

    return violations


def self_test() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        fixture = src / "fixture.rs"

        # ── flagged: tuple variant with multiple fields ───────────────

        fixture.write_text(
            "pub enum Foo {\n"
            "    BadVariant(String, u32),\n"
            "    Unit,\n"
            "}\n"
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: multi-field tuple variant not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        if "BadVariant" not in violations[0].message:
            print(f"self-test: wrong message for tuple variant: {violations[0].message}", file=sys.stderr)
            return 1

        if violations[0].line != 2:
            print(f"self-test: wrong line for tuple variant: {violations[0].line}", file=sys.stderr)
            return 1

        # ── flagged: single-field tuple variant ───────────────────────

        fixture.write_text(
            "pub enum Foo {\n"
            "    Single(String),\n"
            "}\n"
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: single-field tuple variant not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        # ── flagged: empty tuple variant ──────────────────────────────

        fixture.write_text(
            "pub enum Foo {\n"
            "    Empty(),\n"
            "}\n"
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: empty tuple variant not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        # ── not flagged: named fields ─────────────────────────────────

        fixture.write_text(
            "pub enum Foo {\n"
            "    Named { name: String, age: u32 },\n"
            "    EmptyNamed {},\n"
            "}\n"
        )
        if check(root):
            print("self-test: named-field variants were rejected", file=sys.stderr)
            return 1

        # ── not flagged: unit variants and discriminants ──────────────

        fixture.write_text(
            "pub enum Foo {\n"
            "    A,\n"
            "    B = 5,\n"
            "    C = 1 << 4,\n"
            "}\n"
        )
        if check(root):
            print("self-test: unit/discriminant variants were rejected", file=sys.stderr)
            return 1

        # ── mixed enum: only the tuple variant is flagged ─────────────

        fixture.write_text(
            "pub enum Foo {\n"
            "    Unit,\n"
            "    Named { name: String },\n"
            "    Tuple(String),\n"
            "    Unit2,\n"
            "}\n"
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: mixed enum reported {len(violations)}; expected 1", file=sys.stderr)
            return 1

        if "Tuple" not in violations[0].message:
            print(f"self-test: wrong variant flagged in mixed enum: {violations[0].message}", file=sys.stderr)
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="no-unnamed-fields", description="Forbid unnamed (tuple) fields in enum variants")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = args.root.resolve()
    violations: list[Violation] = []

    if args.paths:
        for path in args.paths:
            resolved = path.resolve()
            files = [resolved] if resolved.is_file() else sorted(resolved.rglob("*.rs"))
            for file in files:
                source = file.read_bytes()
                visible_source = production_source(file, root)
                violations.extend(check_file(file, root, visible_source))
    else:
        violations = check(root)

    for violation in violations:
        print(f"{violation.path}:{violation.line}: {violation.code}: {violation.message}", file=sys.stderr)

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
