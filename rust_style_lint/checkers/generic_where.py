"""Enforce canonical generic bounds and where clauses."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))
DECLARATION_TYPES = {
    "enum_item",
    "function_item",
    "impl_item",
    "struct_item",
    "trait_item",
    "type_item",
    "union_item",
}
BOUND_PARAMETER_TYPES = {"lifetime_parameter", "type_parameter"}


def node_text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def declaration_name(node: tree_sitter.Node, source: bytes) -> str:
    name = node.child_by_field_name("name")

    if name is not None:
        return node_text(source, name)

    return "impl"


def generic_parameters(node: tree_sitter.Node) -> list[tree_sitter.Node]:
    parameters = node.child_by_field_name("type_parameters")

    if parameters is None:
        return []

    return [
        parameter
        for parameter in parameters.named_children
        if parameter.type in BOUND_PARAMETER_TYPES
    ]


def is_return_position(node: tree_sitter.Node) -> bool:
    # `impl Trait` in return position is (possibly wrapped by a `+ Bound`
    # `bounded_type`) the return-type field of a function-like declaration.
    cursor = node

    while cursor.parent is not None and cursor.parent.type == "bounded_type":
        cursor = cursor.parent

    parent = cursor.parent

    if parent is None:
        return False

    return parent.child_by_field_name("return_type") == cursor


def repeated_where_predicates(
    node: tree_sitter.Node,
    source: bytes,
) -> list[tuple[tree_sitter.Node, str]]:
    seen: set[bytes] = set()
    repeated: list[tuple[tree_sitter.Node, str]] = []

    for predicate in node.named_children:
        if predicate.type != "where_predicate":
            continue

        left = predicate.child_by_field_name("left")

        if left is None:
            continue

        key = source[left.start_byte : left.end_byte]

        if key in seen:
            repeated.append((predicate, key.decode()))
            continue

        seen.add(key)

    return repeated


def check_file(path: Path, root: Path) -> list[Violation]:
    source = production_source(path, root)
    tree = PARSER.parse(source)
    violations: list[Violation] = []
    pending = [tree.root_node]

    while pending:
        node = pending.pop()

        if node.type in DECLARATION_TYPES:
            for parameter in generic_parameters(node):
                bounds = parameter.child_by_field_name("bounds")

                if bounds is None:
                    continue

                name = parameter.child_by_field_name("name")
                parameter_name = node_text(source, name) if name is not None else "?"
                violations.append(
                    Violation(
                        path=path.relative_to(root),
                        line=parameter.start_point.row + 1,
                        code="GEN001",
                        message=(
                            f"generic parameter {parameter_name} in "
                            f"{declaration_name(node, source)} uses an inline bound; "
                            "move the bound to a where clause"
                        ),
                    ),
                )

        if node.type == "abstract_type" and not is_return_position(node):
            violations.append(
                Violation(
                    path=path.relative_to(root),
                    line=node.start_point.row + 1,
                    code="GEN002",
                    message=(
                        "inline impl Trait is forbidden; introduce a named "
                        "generic parameter and move the bound to a where clause"
                    ),
                ),
            )

        if node.type == "where_clause":
            for predicate, left in repeated_where_predicates(node, source):
                violations.append(
                    Violation(
                        path=path.relative_to(root),
                        line=predicate.start_point.row + 1,
                        code="GEN003",
                        message=(
                            f"where predicate for {left} is repeated; "
                            f"merge all bounds for {left} into one predicate"
                        ),
                    ),
                )

        pending.extend(reversed(node.named_children))

    return violations


def excluded(path: Path, root: Path, config: dict | None) -> bool:
    exclude_files = (config or {}).get("exclude_files", [])

    for pattern in exclude_files:
        if path.relative_to(root) == Path(pattern):
            return True

    return False


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return violations for non-canonical generic bounds and where clauses."""
    return [
        violation
        for path in sorted((root / "src").rglob("*.rs"))
        if not excluded(path, root, config)
        for violation in check_file(path, root)
    ]


def self_test() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source_dir = root / "src"
        source_dir.mkdir()
        source = source_dir / "generic.rs"
        source.write_text(
            "fn clean<T>() {}\n"
            "fn constrained<T>() where T: Copy {}\n"
            "struct Item<T> where T: Copy {}\n"
            "impl<T> Item<T> where T: Copy {}\n"
            "fn merged<T>() where T: Copy + Send {}\n"
            "fn distinct<T, U>() where T: Copy, U: Send {}\n"
            "fn return_opaque() -> impl Iterator<Item = u8> { todo!() }\n"
            "#[cfg(test)]\n"
            "mod tests { fn ignored<T: Copy>() {} }\n",
        )

        if check(root):
            print("self-test: valid or test-only bounds were rejected", file=sys.stderr)
            return 1

        source.write_text(
            "fn bad_fn<T: Copy, 'a: 'static>() {}\n"
            "impl<T: Copy> Item<T> {}\n"
            "struct BadStruct<T: Copy> {}\n"
            "enum BadEnum<T: Copy> {}\n"
            "trait BadTrait<T: Copy> {}\n"
            "type BadAlias<T: Copy> = Vec<T>;\n"
            "union BadUnion<T: Copy> { value: T }\n"
            "fn bad_impl_trait(develop: &(impl EffectDevelop + Sync), other: impl Other) {}\n"
            "fn repeated<T>() where T: Copy, T: Send {}\n"
            "#[cfg(any(test, feature = \"extra\"))]\n"
            "mod maybe_production { fn bad_mod<T: Copy>() {} }\n",
        )
        violations = check(root)

        codes = [violation.code for violation in violations]

        if (
            codes.count("GEN001") != 9
            or codes.count("GEN002") != 2
            or codes.count("GEN003") != 1
        ):
            print("self-test: inline generic syntax was not fully diagnosed", file=sys.stderr)
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="generic-where")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    violations = check(args.root.resolve())

    for violation in violations:
        print(f"{violation.path}:{violation.line}: {violation.code}: {violation.message}", file=sys.stderr)

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
