"""Enforce declaration and helper ordering in hand-written Rust source."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation, source_files
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))


def node_text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def declaration_name(node: tree_sitter.Node, source: bytes) -> str | None:
    name = node.child_by_field_name("name")

    return node_text(source, name) if name is not None else None


def impl_type_name(node: tree_sitter.Node, source: bytes) -> str | None:
    type_node = node.child_by_field_name("type")

    if type_node is None:
        return None

    identifiers: list[tree_sitter.Node] = []
    pending = [type_node]

    while pending:
        current = pending.pop()

        if current.type == "type_identifier":
            identifiers.append(current)

        pending.extend(reversed(current.named_children))

    return node_text(source, identifiers[-1]) if identifiers else None


def is_inherent_impl(node: tree_sitter.Node) -> bool:
    return node.child_by_field_name("trait") is None


def is_public(function: tree_sitter.Node, source: bytes) -> bool:
    name = function.child_by_field_name("name")

    if name is None:
        return False

    return source[function.start_byte : name.start_byte].lstrip().startswith(b"pub")


def is_test_only(node: tree_sitter.Node, source: bytes) -> bool:
    current: tree_sitter.Node | None = node

    while current is not None:
        prefix = source[current.start_byte : current.end_byte]

        if current.type == "attribute_item" and b"cfg(test)" in prefix.replace(b" ", b""):
            return True

        previous = current.prev_named_sibling

        while previous is not None and previous.type == "attribute_item":
            attribute = node_text(source, previous).replace(" ", "")

            if attribute.startswith("#[cfg(test)]"):
                return True

            previous = previous.prev_named_sibling

        current = current.parent

    return False


def container_items(
    container: tree_sitter.Node,
    source: bytes,
) -> list[tree_sitter.Node]:
    return [
        child
        for child in container.named_children
        if child.type not in {"attribute_item", "line_comment", "block_comment"}
    ]


def violations_for_struct_order(
    container: tree_sitter.Node,
    path: Path,
    root: Path,
    source: bytes,
) -> list[Violation]:
    declarations = [
        child for child in container_items(container, source) if not is_test_only(child, source)
    ]
    violations: list[Violation] = []

    for index, declaration in enumerate(declarations):
        if declaration.type != "struct_item":
            continue

        struct_name = declaration_name(declaration, source)

        if struct_name is None:
            continue

        matching = [
            candidate
            for candidate in declarations[index + 1 :]
            if candidate.type == "impl_item" and impl_type_name(candidate, source) == struct_name
        ]

        if not matching:
            continue

        adjacent = declarations[index + 1 : index + 1 + len(matching)]

        if adjacent != matching:
            offending = next(
                candidate
                for candidate in matching
                if candidate not in adjacent or adjacent.index(candidate) != matching.index(candidate)
            )
            violations.append(
                Violation(
                    path=path.relative_to(root),
                    line=offending.start_point.row + 1,
                    code="LAYOUT001",
                    message=f"impl for {struct_name} must immediately follow its struct declaration",
                ),
            )

        trait_seen = False

        for implementation in matching:
            if is_inherent_impl(implementation):
                if trait_seen:
                    violations.append(
                        Violation(
                            path=path.relative_to(root),
                            line=implementation.start_point.row + 1,
                            code="LAYOUT002",
                            message=f"inherent impl for {struct_name} must precede its trait impls",
                        ),
                    )
            else:
                trait_seen = True

    return violations


def called_name(call: tree_sitter.Node, source: bytes) -> str | None:
    function = call.child_by_field_name("function")

    if function is None:
        return None

    text = node_text(source, function)
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", text)

    return match.group(1) if match is not None else None


def calls_in(function: tree_sitter.Node, source: bytes) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    pending = list(reversed(function.named_children))

    while pending:
        current = pending.pop()

        if current.type == "function_item":
            continue

        if current.type == "call_expression":
            name = called_name(current, source)

            if name is not None:
                calls.append((name, current.start_byte))

        pending.extend(reversed(current.named_children))

    return calls


def violations_for_functions(
    container: tree_sitter.Node,
    path: Path,
    root: Path,
    source: bytes,
) -> list[Violation]:
    functions = [
        child
        for child in container.named_children
        if child.type == "function_item" and not is_test_only(child, source)
    ]
    private_functions = [function for function in functions if not is_public(function, source)]
    public_functions = [function for function in functions if is_public(function, source)]
    violations: list[Violation] = []

    if public_functions and private_functions:
        last_public = max(function.start_byte for function in public_functions)
        first_private = min(function.start_byte for function in private_functions)

        if last_public > first_private:
            first_private_function = next(
                function
                for function in private_functions
                if function.start_byte == first_private
            )
            violations.append(
                Violation(
                    path=path.relative_to(root),
                    line=first_private_function.start_point.row + 1,
                    code="LAYOUT003",
                    message="private functions must follow all public functions",
                ),
            )

    private_names = {
        declaration_name(function, source): function
        for function in private_functions
        if declaration_name(function, source) is not None
    }
    first_calls: dict[str, int] = {}

    for function in functions:
        for name, offset in calls_in(function, source):
            if name in private_names:
                first_calls[name] = min(first_calls.get(name, offset), offset)

    expected = sorted(
        private_functions,
        key=lambda function: (
            first_calls.get(declaration_name(function, source) or "", sys.maxsize),
            function.start_byte,
        ),
    )

    if private_functions != expected:
        first_wrong = next(
            current
            for current, expected_function in zip(private_functions, expected)
            if current != expected_function
        )
        expected_name = declaration_name(expected[private_functions.index(first_wrong)], source)
        violations.append(
            Violation(
                path=path.relative_to(root),
                line=first_wrong.start_point.row + 1,
                code="LAYOUT004",
                message=(
                    f"private function {declaration_name(first_wrong, source)} must follow "
                    f"first-call order; {expected_name} is called earlier"
                ),
            ),
        )

    return violations


def check_container(
    container: tree_sitter.Node,
    path: Path,
    root: Path,
    source: bytes,
) -> list[Violation]:
    violations = violations_for_struct_order(container, path, root, source)
    violations.extend(violations_for_functions(container, path, root, source))

    for child in container.named_children:
        if child.type == "impl_item":
            body = child.child_by_field_name("body")

            if body is not None:
                violations.extend(check_container(body, path, root, source))
        elif child.type == "mod_item":
            body = child.child_by_field_name("body")

            if body is not None:
                violations.extend(check_container(body, path, root, source))

    return violations


def excluded(path: Path, root: Path, config: dict | None) -> bool:
    exclude_files = (config or {}).get("exclude_files", [])

    for pattern in exclude_files:
        if path.relative_to(root) == Path(pattern):
            return True

    return False


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return declaration-ordering violations under src/."""
    violations: list[Violation] = []

    for path in source_files(root):
        if excluded(path, root, config):
            continue

        source = production_source(path, root)
        violations.extend(
            check_container(PARSER.parse(source).root_node, path, root, source),
        )

    return violations


def self_test() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "src" / "layout.rs"
        source.parent.mkdir(parents=True)
        source.write_text(
            "pub struct Good;\n"
            "impl Good { pub fn create() {} fn prepare() {} }\n"
            "impl Default for Good { fn default() -> Self { Self } }\n"
            "pub fn run() { prepare(); finish(); }\n"
            "fn prepare() {}\n"
            "fn finish() {}\n",
        )

        if check(root):
            print("self-test: valid layout fixture was rejected", file=sys.stderr)
            return 1

        source.write_text(
            "pub struct Wrong;\n"
            "const SEPARATES_WRONG_IMPL: () = ();\n"
            "impl Default for Wrong { fn default() -> Self { Self } }\n"
            "impl Wrong { fn create() {} }\n"
            "/// Runs the private helpers.\n"
            "fn second() {}\n"
            "/// Calls helpers in their required order.\n"
            "pub fn run() { first(); second(); }\n"
            "/// Runs before the second helper.\n"
            "fn first() {}\n",
        )
        violations = check(root)
        codes = {violation.code for violation in violations}

        if codes != {"LAYOUT001", "LAYOUT002", "LAYOUT003", "LAYOUT004"}:
            print("self-test: layout violations were not fully diagnosed", file=sys.stderr)
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="item-layout")
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
