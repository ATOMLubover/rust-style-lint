"""Forbid type-annotation hints on let bindings.

Prefer inference, and turbofish when a type must be pinned explicitly::

    let x = expr.collect::<Vec<_>>();       // GOOD
    let y = resolver.parse::<u32>()?;       // GOOD — turbofish on the call
    let z: u32 = expr.parse()?;             // BAD — type hint on the let binding

The rule is uniform: no `let x: T = value` is allowed. When the value's type
cannot be inferred, supply it as turbofish on the value's generic call instead
of annotating the binding.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation, source_files
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))


def text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def line_number(source: bytes, offset: int) -> int:
    return source.count(b"\n", 0, offset) + 1


def descendants(node: tree_sitter.Node, kind: str) -> list[tree_sitter.Node]:
    found: list[tree_sitter.Node] = []
    pending = [node]

    while pending:
        current = pending.pop()

        if current.type == kind:
            found.append(current)

        pending.extend(reversed(current.named_children))

    return found


def type_annotation_text(node: tree_sitter.Node, source: bytes) -> str | None:
    """Extract the type annotation string from a let_declaration."""
    type_node = node.child_by_field_name("type")

    if type_node is not None:
        return text(source, type_node)

    return None


def check_file(path: Path, root: Path, source: bytes) -> list[Violation]:
    tree = PARSER.parse(source)
    violations: list[Violation] = []

    for let_node in descendants(tree.root_node, "let_declaration"):
        if type_annotation_text(let_node, source) is None:
            continue

        type_ann = type_annotation_text(let_node, source)
        violations.append(
            Violation(
                path=path.relative_to(root),
                line=line_number(source, let_node.start_byte),
                code="NO_TYPE_HINT",
                message=(
                    f"type hint `{type_ann}` on let binding; remove the annotation and rely on inference, "
                    "or pin the type with turbofish on the value's generic call"
                ),
            ),
        )

    return violations


def fix_file(path: Path, source: bytes) -> tuple[bytes, bool]:
    """Conservatively leave the source untouched.

    Removing an annotation can silently change the inferred type (literals,
    `as` casts, generic constructors) or break compilation entirely (Diesel
    `.first()?` / `.load()`), and turbofish is not valid on every method.
    Auto-editing is therefore a no-op: the check reports, and the developer
    applies the recommended fix by hand.
    """
    return source, False


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

    return source_files(root)


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return violations for let bindings carrying a type annotation."""
    violations: list[Violation] = []

    for path in rust_files(root, []):
        source = path.read_bytes()
        visible_source = production_source(path, root)
        violations.extend(check_file(path, root, visible_source))

    return violations


def fix(root: Path, config: dict | None = None) -> list[Violation]:
    """Accepted for compatibility; auto-fix is not implemented."""
    return check(root, config)


def main() -> int:
    parser = argparse.ArgumentParser(prog="no-type-hint", description="Forbid type hints on let bindings")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--fix", action="store_true", help="accepted for compatibility; auto-fix is not implemented")
    args = parser.parse_args()

    root = args.root.resolve()
    violations: list[Violation] = []

    for path in rust_files(root, args.paths):
        source = path.read_bytes()
        visible_source = production_source(path, root)
        violations.extend(check_file(path, root, visible_source))

    if args.fix:
        print(
            "note: --fix is a no-op; remove or turbofish the annotation by hand "
            "(auto-editing can change the inferred type or break compilation)",
            file=sys.stderr,
        )

    for violation in violations:
        print(f"{violation.path}:{violation.line}: {violation.code}: {violation.message}", file=sys.stderr)

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
