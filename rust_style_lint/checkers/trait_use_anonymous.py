"""Require method-resolution-only trait imports to use ``as _``."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation
from ..config import merged
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))


@dataclass(frozen=True)
class TraitImport:
    path: str
    local_name: str
    alias: str | None
    line: int


def rust_files(root: Path) -> list[Path]:
    excluded = {".git", ".venv", "target", "node_modules"}

    return sorted(
        path
        for path in root.rglob("*.rs")
        if not any(part in excluded for part in path.parts)
    )


def node_text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def descendants(node: tree_sitter.Node, kind: str) -> list[tree_sitter.Node]:
    found: list[tree_sitter.Node] = []
    nodes = [node]

    while nodes:
        current = nodes.pop()

        if current.type == kind:
            found.append(current)

        nodes.extend(reversed(current.named_children))

    return found


def path_parts(node: tree_sitter.Node, source: bytes) -> list[str]:
    return node_text(source, node).removesuffix("::").split("::")


def use_leaves(
    node: tree_sitter.Node,
    source: bytes,
    prefix: list[str] | None = None,
) -> list[tuple[str, str | None, tree_sitter.Node]]:
    """Flatten one use tree into (path, alias, leaf-node) tuples."""

    prefix = prefix or []

    if node.type == "use_declaration":
        return [
            leaf
            for child in node.named_children
            if child.type != "visibility_modifier"
            for leaf in use_leaves(child, source)
        ]

    if node.type == "scoped_use_list":
        base, use_list = node.named_children

        return use_leaves(use_list, source, prefix + path_parts(base, source))

    if node.type == "use_list":
        return [
            leaf
            for child in node.named_children
            for leaf in use_leaves(child, source, prefix)
        ]

    if node.type == "use_as_clause":
        path_node, alias_node = node.named_children
        path = prefix + path_parts(path_node, source)

        return [("::".join(path), node_text(source, alias_node), path_node)]

    if node.type == "scoped_identifier":
        path = prefix + path_parts(node, source)

        return [("::".join(path), None, node)]

    if node.type == "identifier":
        path = prefix + [node_text(source, node)]

        return [("::".join(path), None, node)]

    return []


def is_inside_use(node: tree_sitter.Node) -> bool:
    current = node.parent

    while current is not None:
        if current.type == "use_declaration":
            return True

        current = current.parent

    return False


def is_type_name(name: str) -> bool:
    significant = name.lstrip("_")

    return bool(significant) and significant[0].isupper()


def explicitly_used(
    tree: tree_sitter.Tree,
    source: bytes,
    imported: TraitImport,
    macro_markers: list[str],
) -> bool:
    for macro in descendants(tree.root_node, "macro_invocation"):
        if any(marker in node_text(source, macro) for marker in macro_markers):
            return True

        if re.search(
            rf"\b{re.escape(imported.local_name)}\b",
            node_text(source, macro),
        ):
            return True

    for node in descendants(tree.root_node, "identifier") + descendants(
        tree.root_node,
        "type_identifier",
    ):
        if node_text(source, node) != imported.local_name:
            continue

        if is_inside_use(node):
            continue

        return True

    return False


def imports_in_file(
    path: Path,
    root: Path,
) -> list[TraitImport]:
    source = production_source(path, root)
    tree = PARSER.parse(source)
    imports: list[TraitImport] = []

    for declaration in descendants(tree.root_node, "use_declaration"):
        if any(
            child.type == "visibility_modifier"
            for child in declaration.named_children
        ):
            continue

        for path_name, alias, path_node in use_leaves(declaration, source):
            if path_name.rsplit("::", 1)[-1] == "self":
                continue

            local_name = alias or path_name.rsplit("::", 1)[-1]

            if alias == "_" or not is_type_name(local_name):
                continue

            imports.append(
                TraitImport(
                    path=path_name,
                    local_name=local_name,
                    alias=alias,
                    line=path_node.start_point.row + 1,
                )
            )

    return imports


def check_file(
    path: Path,
    root: Path,
    macro_markers: list[str],
) -> list[Violation]:
    source = production_source(path, root)
    tree = PARSER.parse(source)
    violations: list[Violation] = []

    for imported in imports_in_file(path, root):
        if explicitly_used(tree, source, imported, macro_markers):
            continue

        violations.append(
            Violation(
                path=path.relative_to(root),
                line=imported.line,
                code="TRAIT001",
                message=(
                    f"trait import `{imported.path}` is only used for method resolution; "
                    "import it as `_`"
                ),
            ),
        )

    return violations


def configured_macro_markers(config: dict) -> list[str]:
    return [str(marker) for marker in config.get("macro_markers", [])]


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return method-resolution-only trait imports under the project root."""
    root = root.resolve()
    section = merged("trait-use-anonymous", config)
    macro_markers = configured_macro_markers(section)

    return [
        violation
        for path in rust_files(root)
        for violation in check_file(path, root, macro_markers)
    ]


def self_test() -> int:
    config = {
        "macro_markers": ["preloadable!"],
    }

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir(parents=True)
        (src / "traits.rs").write_text(
            "pub trait MethodTrait { fn ping(&self); }\n"
            "pub trait NamedTrait { fn named(&self); }\n",
        )
        (src / "lib.rs").write_text(
            "mod traits;\n"
            "use crate::traits::MethodTrait;\n"
            "use crate::traits::NamedTrait;\n"
            "use poprako_util::time::ToUnixMilli;\n"
            "struct Value;\n"
            "impl NamedTrait for Value { fn named(&self) {} }\n"
            "fn call(value: &Value) { value.ping(); }\n"
            "fn millis(value: &Value) { value.to_unix_milli(); }\n"
            "fn bound<T: NamedTrait>() {}\n",
        )

        violations = check(root, config)

        if len(violations) != 2 or not any(
            "MethodTrait" in violation.message for violation in violations
        ):
            print("self-test: method-only trait import was not detected", file=sys.stderr)
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

        (src / "lib.rs").write_text(
            "mod traits;\n"
            "use crate::traits::MethodTrait as _;\n"
            "use crate::traits::NamedTrait;\n"
            "use poprako_util::time::ToUnixMilli as _;\n"
            "struct Value;\n"
            "impl NamedTrait for Value { fn named(&self) {} }\n"
            "fn call(value: &Value) { value.ping(); }\n"
            "fn millis(value: &Value) { value.to_unix_milli(); }\n"
            "fn bound<T: NamedTrait>() {}\n",
        )

        if check(root, config):
            print("self-test: valid trait imports were rejected", file=sys.stderr)
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="trait-use-anonymous")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    violations = check(args.root.resolve(), None)

    for violation in violations:
        print(f"{violation.path}:{violation.line}: {violation.code}: {violation.message}", file=sys.stderr)

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
