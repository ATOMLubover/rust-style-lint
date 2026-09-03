"""Forbid naming a value only to destructure it in the next statement.

Bind the destructuring pattern directly to the producing expression::

    let (left, right) = load_pair()?;             # GOOD

    let pair = load_pair()?;                      # BAD
    let (left, right) = pair;

The check is deliberately limited to adjacent statements. A comment or any
other statement between the bindings may document or use the intermediate
value and therefore prevents a violation.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation, source_files
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))

DESTRUCTURING_PATTERNS = {
    "slice_pattern",
    "struct_pattern",
    "tuple_pattern",
    "tuple_struct_pattern",
}


def node_text(source: bytes, node: tree_sitter.Node) -> str:
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


def is_destructuring_pattern(node: tree_sitter.Node) -> bool:
    pending = [node]

    while pending:
        current = pending.pop()

        if current.type in DESTRUCTURING_PATTERNS:
            return True

        pending.extend(current.named_children)

    return False


def redundant_pair(
    first: tree_sitter.Node,
    second: tree_sitter.Node,
    source: bytes,
) -> tuple[str, str] | None:
    if first.type != "let_declaration" or second.type != "let_declaration":
        return None

    temporary = first.child_by_field_name("pattern")
    expression = first.child_by_field_name("value")
    pattern = second.child_by_field_name("pattern")
    value = second.child_by_field_name("value")

    if temporary is None or expression is None or pattern is None or value is None:
        return None

    if second.child_by_field_name("alternative") is not None:
        return None

    if temporary.type != "identifier" or not is_destructuring_pattern(pattern):
        return None

    if value.type != "identifier" or node_text(source, temporary) != node_text(source, value):
        return None

    return node_text(source, temporary), node_text(source, pattern)


def check_file(path: Path, root: Path, source: bytes) -> list[Violation]:
    tree = PARSER.parse(source)
    violations: list[Violation] = []

    for block in descendants(tree.root_node, "block"):
        children = block.named_children

        for first, second in zip(children, children[1:]):
            pair = redundant_pair(first, second, source)

            if pair is None:
                continue

            temporary, pattern = pair
            violations.append(
                Violation(
                    path=path.relative_to(root),
                    line=first.start_point.row + 1,
                    column=first.start_point.column + 1,
                    code="DSTR001",
                    message=(
                        f"temporary `{temporary}` is immediately destructured; "
                        f"bind `{pattern}` directly to the original expression"
                    ),
                ),
            )

    return violations


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return immediately destructured temporary bindings."""
    violations: list[Violation] = []

    for path in source_files(root):
        source = production_source(path, root)
        violations.extend(check_file(path, root, source))

    return violations
