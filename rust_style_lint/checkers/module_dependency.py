"""Enforce downward-or-across Rust module dependencies and reject cycles."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation


PATH_KINDS = {"scoped_identifier", "scoped_type_identifier"}
IDENTIFIER_KINDS = {"identifier", "type_identifier"}
# Module-level definitions whose names must not collide across modules.
NAME_ITEM_KINDS = {"type_item", "struct_item", "trait_item"}
KIND_LABELS = {"type_item": "type alias", "struct_item": "struct", "trait_item": "trait"}
PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))
INTERNAL_ATTRIBUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:crate|self|super)(?:::[A-Za-z_][A-Za-z0-9_]*)+"
)


@dataclass(frozen=True)
class Edge:
    source: tuple[str, ...]
    target: tuple[str, ...]
    path: Path
    line: int
    column: int
    reference: str


@dataclass(frozen=True)
class UseLeaf:
    path: str
    alias: str | None
    wildcard: bool


def node_text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def format_module(path: tuple[str, ...]) -> str:
    return "crate" if not path else "crate::" + "::".join(path)


def file_module(src_dir: Path, path: Path) -> tuple[str, ...]:
    relative = path.relative_to(src_dir)

    if relative.name in {"lib.rs", "main.rs"} and len(relative.parts) == 1:
        return ()

    parts = list(relative.parts)

    if parts[-1] == "mod.rs":
        parts.pop()
    else:
        parts[-1] = Path(parts[-1]).stem

    return tuple(parts)


def leading_attributes(node: tree_sitter.Node) -> list[tree_sitter.Node]:
    parent = node.parent

    if parent is None:
        return []

    index = next(
        (
            index
            for index, sibling in enumerate(parent.children)
            if sibling.start_byte == node.start_byte
            and sibling.end_byte == node.end_byte
        ),
        None,
    )

    if index is None:
        return []

    attributes: list[tree_sitter.Node] = []

    for sibling in reversed(parent.children[:index]):
        if sibling.type == "attribute_item":
            attributes.append(sibling)
            continue

        # Comments may sit between an outer attribute and its item. They do
        # not end the attribute run, and formatting checkers intentionally
        # keep such comments attached to the item they describe.
        if sibling.type in {"line_comment", "block_comment"}:
            continue

        if not sibling.is_named:
            continue

        break

    attributes.reverse()

    return attributes


class CfgParser:
    """Evaluate possible cfg truth values while forcing `test = false`."""

    TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*|[(),=]')

    def __init__(self, expression: str) -> None:
        self.tokens = self.TOKEN.findall(expression)
        self.index = 0

    def parse(self) -> set[bool]:
        values = self._expression()

        if self.index != len(self.tokens):
            return {False, True}

        return values

    def _expression(self) -> set[bool]:
        if self.index >= len(self.tokens):
            return {False, True}

        name = self.tokens[self.index]
        self.index += 1

        if self._take("="):
            if self.index < len(self.tokens):
                self.index += 1

            return {False, True}

        if not self._take("("):
            return {False} if name == "test" else {False, True}

        arguments: list[set[bool]] = []

        while self.index < len(self.tokens) and self.tokens[self.index] != ")":
            arguments.append(self._expression())

            if not self._take(","):
                break

        if not self._take(")"):
            return {False, True}

        if name == "all":
            return {all(values) for values in products(arguments)}

        if name == "any":
            return {any(values) for values in products(arguments)}

        if name == "not" and len(arguments) == 1:
            return {not value for value in arguments[0]}

        return {False, True}

    def _take(self, expected: str) -> bool:
        if self.index >= len(self.tokens) or self.tokens[self.index] != expected:
            return False

        self.index += 1

        return True


def products(sets: list[set[bool]]) -> list[tuple[bool, ...]]:
    result: list[tuple[bool, ...]] = [()]

    for values in sets:
        result = [prefix + (value,) for prefix in result for value in values]

    return result


def cfg_expression(attribute: str) -> str | None:
    match = re.fullmatch(r"\s*#\s*\[\s*cfg\s*\((.*)\)\s*]\s*", attribute, re.DOTALL)

    return match.group(1) if match is not None else None


def has_test_only_cfg(node: tree_sitter.Node, source: bytes) -> bool:
    current: tree_sitter.Node | None = node

    while current is not None:
        for attribute in leading_attributes(current):
            expression = cfg_expression(node_text(source, attribute))

            if expression is not None and True not in CfgParser(expression).parse():
                return True

        current = current.parent

    return False


def module_path(base: tuple[str, ...], node: tree_sitter.Node, source: bytes) -> tuple[str, ...]:
    inline_modules: list[str] = []
    current = node.parent

    while current is not None:
        if current.type == "mod_item" and current.child_by_field_name("body") is not None:
            name = current.child_by_field_name("name")

            if name is not None:
                inline_modules.append(node_text(source, name))

        current = current.parent

    return base + tuple(reversed(inline_modules))


def starts_with(path: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(path) >= len(prefix) and path[: len(prefix)] == prefix


def excluded_module(path: tuple[str, ...], prefixes: set[tuple[str, ...]]) -> bool:
    return any(starts_with(path, prefix) for prefix in prefixes)


def all_rust_files(root: Path) -> list[Path]:
    src_dir = root / "src"

    if not src_dir.is_dir():
        return []

    return sorted(src_dir.rglob("*.rs"))


def excluded_path(path: Path, root: Path, config: dict | None) -> bool:
    exclude_files = (config or {}).get("exclude_files", [])

    return path.relative_to(root) in {Path(pattern) for pattern in exclude_files}


def discover_crate(
    root: Path,
    config: dict | None,
) -> tuple[list[Path], set[tuple[str, ...]], set[tuple[str, ...]]]:
    src_dir = root / "src"
    paths = all_rust_files(root)
    files_by_module: dict[tuple[str, ...], Path] = {}

    for path in paths:
        module = file_module(src_dir, path)

        if module or path.name == "lib.rs":
            files_by_module.setdefault(module, path)

    roots = [path for path in (src_dir / "lib.rs", src_dir / "main.rs") if path.is_file()]
    pending_paths = list(reversed(roots))
    scanned: set[Path] = set()
    modules: set[tuple[str, ...]] = {()}
    prefixes: set[tuple[str, ...]] = set()

    while pending_paths:
        path = pending_paths.pop()

        if path in scanned:
            continue

        scanned.add(path)

        if excluded_path(path, root, config):
            continue

        source = path.read_bytes()
        tree = PARSER.parse(source)
        base = file_module(src_dir, path)
        pending = [tree.root_node]

        while pending:
            current = pending.pop()

            if current.type == "mod_item":
                name = current.child_by_field_name("name")

                if name is None:
                    continue

                child_module = module_path(base, current, source) + (node_text(source, name),)

                if has_test_only_cfg(current, source):
                    prefixes.add(child_module)
                    continue

                modules.add(child_module)

                if current.child_by_field_name("body") is None:
                    child_path = files_by_module.get(child_module)

                    if child_path is not None:
                        pending_paths.append(child_path)

                    continue

            pending.extend(reversed(current.named_children))

    scan_paths = sorted(
        path
        for path in scanned
        if not excluded_path(path, root, config)
    )

    return scan_paths, modules, prefixes


def read_crate_name(root: Path) -> str | None:
    cargo_toml = root / "Cargo.toml"

    if not cargo_toml.is_file():
        return None

    in_package = False

    for raw_line in cargo_toml.read_text().splitlines():
        line = raw_line.strip()

        if line.startswith("[") and line.endswith("]"):
            in_package = line == "[package]"
            continue

        if not in_package:
            continue

        match = re.fullmatch(r'name\s*=\s*"([^"]+)"', line)

        if match is not None:
            return match.group(1).replace("-", "_")

    return None


def split_top_level(expression: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0

    for index, character in enumerate(expression):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(expression[start:index])
            start = index + 1

    parts.append(expression[start:])

    return [part.strip() for part in parts if part.strip()]


def matching_brace(expression: str, left: int) -> int | None:
    depth = 0

    for index in range(left, len(expression)):
        if expression[index] == "{":
            depth += 1
        elif expression[index] == "}":
            depth -= 1

            if depth == 0:
                return index

    return None


def join_path(prefix: str, suffix: str) -> str:
    left = prefix.strip().strip(":")
    right = suffix.strip().strip(":")

    if not left:
        return right

    if not right:
        return left

    return left + "::" + right


def expand_use_tree(expression: str, prefix: str = "") -> list[UseLeaf]:
    leaves: list[UseLeaf] = []

    for part in split_top_level(expression.removeprefix("::")):
        left = part.find("{")

        if left >= 0:
            right = matching_brace(part, left)

            if right is None:
                continue

            branch_prefix = join_path(prefix, part[:left])
            leaves.extend(expand_use_tree(part[left + 1 : right], branch_prefix))
            continue

        alias_parts = re.split(r"\s+as\s+", part, maxsplit=1)
        raw_path = alias_parts[0].strip()
        alias = alias_parts[1].strip() if len(alias_parts) == 2 else None
        wildcard = raw_path == "*"
        full_path = prefix if raw_path == "self" or wildcard else join_path(prefix, raw_path)

        if not full_path:
            continue

        if alias is None and not wildcard:
            alias = full_path.split("::")[-1]

        leaves.append(UseLeaf(full_path, alias, wildcard))

    return leaves


def path_segments(raw_path: str) -> list[str]:
    segments: list[str] = []

    for segment in raw_path.strip().removeprefix("::").split("::"):
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", segment.strip())

        if match is None:
            break

        segments.append(match.group(0))

    return segments


def absolute_path(
    source: tuple[str, ...],
    raw_path: str,
    top_modules: set[str],
    crate_name: str | None,
) -> tuple[str, ...] | None:
    parts = path_segments(raw_path)

    if not parts:
        return None

    first = parts[0]
    rest = parts[1:]

    if first == "crate" or first == crate_name:
        result: list[str] = []
    elif first == "self":
        result = list(source)
    elif first == "super":
        result = list(source[:-1])

        while rest and rest[0] == "super":
            if result:
                result.pop()

            rest.pop(0)
    elif first in top_modules:
        result = []
        rest = parts
    else:
        return None

    for segment in rest:
        if segment == "self":
            continue

        if segment == "super":
            if result:
                result.pop()

            continue

        result.append(segment)

    return tuple(result)


def target_module(
    path: tuple[str, ...],
    modules: set[tuple[str, ...]],
) -> tuple[str, ...] | None:
    for length in range(len(path), -1, -1):
        candidate = path[:length]

        if candidate in modules:
            return candidate

    return None


def is_strict_ancestor(ancestor: tuple[str, ...], path: tuple[str, ...]) -> bool:
    return len(ancestor) < len(path) and path[: len(ancestor)] == ancestor


def inside(node: tree_sitter.Node, ancestor_type: str) -> bool:
    current = node.parent

    while current is not None:
        if current.type == ancestor_type:
            return True

        current = current.parent

    return False


def inside_pure_impl_head(node: tree_sitter.Node) -> bool:
    current: tree_sitter.Node | None = node

    while current is not None:
        if current.type == "impl_item":
            trait = current.child_by_field_name("trait")
            self_type = current.child_by_field_name("type")

            return any(
                candidate is not None
                and candidate.start_byte <= node.start_byte
                and node.end_byte <= candidate.end_byte
                for candidate in (trait, self_type)
            )

        current = current.parent

    return False


def direct_scope(node: tree_sitter.Node) -> tree_sitter.Node:
    current = node.parent

    while current is not None:
        if current.type == "source_file":
            return current

        if current.type == "declaration_list" and current.parent is not None and current.parent.type == "mod_item":
            return current

        current = current.parent

    return node


def direct_scope_nodes(scope: tree_sitter.Node) -> list[tree_sitter.Node]:
    found: list[tree_sitter.Node] = []
    pending = list(reversed(scope.named_children))

    while pending:
        current = pending.pop()
        found.append(current)

        if current.type == "mod_item":
            continue

        pending.extend(reversed(current.named_children))

    return found


def alias_is_pure_impl(
    use_node: tree_sitter.Node,
    alias: str | None,
    source: bytes,
) -> bool:
    if alias is None or alias == "_":
        return False

    occurrences = [
        node
        for node in direct_scope_nodes(direct_scope(use_node))
        if node.type in IDENTIFIER_KINDS
        and node_text(source, node) == alias
        and not inside(node, "use_declaration")
    ]

    return bool(occurrences) and all(inside_pure_impl_head(node) for node in occurrences)


def edge_from_node(
    source_module: tuple[str, ...],
    target: tuple[str, ...],
    path: Path,
    node: tree_sitter.Node,
    reference: str,
) -> Edge:
    return Edge(
        source_module,
        target,
        path,
        node.start_point.row + 1,
        node.start_point.column + 1,
        " ".join(reference.split()),
    )


def collect_use_edges(
    path: Path,
    root: Path,
    source: bytes,
    tree: tree_sitter.Tree,
    modules: set[tuple[str, ...]],
    prefixes: set[tuple[str, ...]],
    crate_name: str | None,
) -> list[Edge]:
    base = file_module(root / "src", path)
    top_modules = {module[0] for module in modules if module}
    edges: list[Edge] = []
    pending = [tree.root_node]

    while pending:
        current = pending.pop()

        if current.type == "use_declaration":
            source_module = module_path(base, current, source)

            if excluded_module(source_module, prefixes) or has_test_only_cfg(current, source):
                pending.extend(reversed(current.named_children))
                continue

            argument = current.child_by_field_name("argument")

            if argument is None:
                pending.extend(reversed(current.named_children))
                continue

            for leaf in expand_use_tree(node_text(source, argument)):
                resolved = absolute_path(source_module, leaf.path, top_modules, crate_name)

                if resolved is None:
                    continue

                target = target_module(resolved, modules)

                if target is None or target == source_module:
                    continue

                if alias_is_pure_impl(current, leaf.alias, source):
                    continue

                edges.append(
                    edge_from_node(
                        source_module,
                        target,
                        path,
                        current,
                        node_text(source, current),
                    )
                )

        pending.extend(reversed(current.named_children))

    return edges


def outer_path_node(node: tree_sitter.Node) -> bool:
    return node.parent is None or node.parent.type not in PATH_KINDS


def collect_qualified_edges(
    path: Path,
    root: Path,
    source: bytes,
    tree: tree_sitter.Tree,
    modules: set[tuple[str, ...]],
    prefixes: set[tuple[str, ...]],
    crate_name: str | None,
) -> list[Edge]:
    base = file_module(root / "src", path)
    top_modules = {module[0] for module in modules if module}
    edges: list[Edge] = []
    pending = [tree.root_node]

    while pending:
        current = pending.pop()

        if current.type in PATH_KINDS and outer_path_node(current) and not inside(current, "use_declaration"):
            source_module = module_path(base, current, source)

            if not excluded_module(source_module, prefixes) and not has_test_only_cfg(current, source):
                raw_path = node_text(source, current)
                resolved = absolute_path(source_module, raw_path, top_modules, crate_name)
                target = target_module(resolved, modules) if resolved is not None else None

                if target is not None and target != source_module and not inside_pure_impl_head(current):
                    edges.append(edge_from_node(source_module, target, path, current, raw_path))

        pending.extend(reversed(current.named_children))

    return edges


def attribute_target(node: tree_sitter.Node) -> tree_sitter.Node | None:
    parent = node.parent

    if parent is None:
        return None

    passed = False

    for sibling in parent.children:
        if sibling.start_byte == node.start_byte and sibling.end_byte == node.end_byte:
            passed = True
            continue

        if not passed or not sibling.is_named or sibling.type == "attribute_item":
            continue

        return sibling

    return None


def collect_attribute_edges(
    path: Path,
    root: Path,
    source: bytes,
    tree: tree_sitter.Tree,
    modules: set[tuple[str, ...]],
    prefixes: set[tuple[str, ...]],
    crate_name: str | None,
) -> list[Edge]:
    base = file_module(root / "src", path)
    top_modules = {module[0] for module in modules if module}
    edges: list[Edge] = []
    pending = [tree.root_node]

    while pending:
        current = pending.pop()

        if current.type == "attribute_item":
            target_item = attribute_target(current)
            source_module = module_path(base, current, source)

            if (
                excluded_module(source_module, prefixes)
                or (target_item is not None and has_test_only_cfg(target_item, source))
            ):
                pending.extend(reversed(current.named_children))
                continue

            attribute = node_text(source, current)

            for match in INTERNAL_ATTRIBUTE_PATH.finditer(attribute):
                raw_path = match.group(0)
                resolved = absolute_path(source_module, raw_path, top_modules, crate_name)
                target = target_module(resolved, modules) if resolved is not None else None

                if target is not None and target != source_module:
                    edges.append(edge_from_node(source_module, target, path, current, raw_path))

        pending.extend(reversed(current.named_children))

    return edges


def deduplicate_edges(edges: list[Edge]) -> list[Edge]:
    unique: dict[tuple[object, ...], Edge] = {}

    for edge in edges:
        key = (edge.source, edge.target, edge.path, edge.line, edge.column, edge.reference)
        unique.setdefault(key, edge)

    return sorted(
        unique.values(),
        key=lambda edge: (
            str(edge.path),
            edge.line,
            edge.column,
            edge.source,
            edge.target,
            edge.reference,
        ),
    )


def collect_edges(root: Path, config: dict | None) -> list[Edge]:
    paths, modules, prefixes = discover_crate(root, config)
    crate_name = read_crate_name(root)
    edges: list[Edge] = []

    for path in paths:
        base = file_module(root / "src", path)

        if excluded_module(base, prefixes):
            continue

        source = path.read_bytes()
        tree = PARSER.parse(source)
        edges.extend(collect_use_edges(path, root, source, tree, modules, prefixes, crate_name))
        edges.extend(collect_qualified_edges(path, root, source, tree, modules, prefixes, crate_name))
        edges.extend(collect_attribute_edges(path, root, source, tree, modules, prefixes, crate_name))

    return deduplicate_edges(edges)


def strongly_connected_components(edges: list[Edge]) -> list[tuple[tuple[str, ...], ...]]:
    adjacency: dict[tuple[str, ...], set[tuple[str, ...]]] = defaultdict(set)

    for edge in edges:
        adjacency[edge.source].add(edge.target)
        adjacency.setdefault(edge.target, set())

    index = 0
    indices: dict[tuple[str, ...], int] = {}
    lowlinks: dict[tuple[str, ...], int] = {}
    stack: list[tuple[str, ...]] = []
    stacked: set[tuple[str, ...]] = set()
    components: list[tuple[tuple[str, ...], ...]] = []

    def visit(module: tuple[str, ...]) -> None:
        nonlocal index

        indices[module] = index
        lowlinks[module] = index
        index += 1
        stack.append(module)
        stacked.add(module)

        for target in sorted(adjacency[module]):
            if target not in indices:
                visit(target)
                lowlinks[module] = min(lowlinks[module], lowlinks[target])
            elif target in stacked:
                lowlinks[module] = min(lowlinks[module], indices[target])

        if lowlinks[module] != indices[module]:
            return

        component: list[tuple[str, ...]] = []

        while stack:
            target = stack.pop()
            stacked.remove(target)
            component.append(target)

            if target == module:
                break

        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for module in sorted(adjacency):
        if module not in indices:
            visit(module)

    return sorted(components)


def edge_violation(edge: Edge, root: Path, code: str, message: str) -> Violation:
    return Violation(
        path=edge.path.relative_to(root),
        line=edge.line,
        column=edge.column,
        code=code,
        message=f"{message}; reference: `{edge.reference}`",
    )


def collect_module_definitions(
    source: bytes,
    module: tuple[str, ...],
    node: tree_sitter.Node,
    definitions: list[tuple[tuple[str, ...], str, str, tree_sitter.Node]],
) -> None:
    """Collect module-scope type alias / struct / trait definitions, descending
    into inline module bodies but not into functions or impl blocks."""
    for child in node.named_children:
        if child.type in NAME_ITEM_KINDS:
            name = child.child_by_field_name("name")

            if name is not None:
                definitions.append((module, child.type, node_text(source, name), child))

        elif child.type == "mod_item":
            body = child.child_by_field_name("body")

            if body is not None:
                mod_name = child.child_by_field_name("name")
                inner = module + ((node_text(source, mod_name),) if mod_name is not None else ())
                collect_module_definitions(source, inner, body, definitions)


def duplicate_name_warnings(root: Path, config: dict | None) -> list[Violation]:
    """Report same-named type aliases / structs / traits defined in two or more
    production modules as non-fatal warnings (a child shadowing a parent's name
    is a smell, but it compiles and must not fail the run)."""
    paths, _, prefixes = discover_crate(root, config)
    by_name: dict[str, list[tuple[tuple[str, ...], Path, str, tree_sitter.Node, bytes]]] = {}

    for path in paths:
        module = file_module(root / "src", path)

        if excluded_module(module, prefixes):
            continue

        source = path.read_bytes()
        definitions: list[tuple[tuple[str, ...], str, str, tree_sitter.Node]] = []
        collect_module_definitions(source, module, PARSER.parse(source).root_node, definitions)

        for def_module, kind, name, node in definitions:
            if excluded_module(def_module, prefixes):
                continue

            by_name.setdefault(name, []).append((def_module, path, kind, node, source))

    warnings: list[Violation] = []

    for name, definitions in sorted(by_name.items()):
        modules_with_name = {definition[0] for definition in definitions}

        if len(modules_with_name) < 2:
            continue

        module_list = ", ".join(format_module(value) for value in sorted(modules_with_name))

        for def_module, path, kind, node, source in definitions:
            warnings.append(
                Violation(
                    path=path.relative_to(root),
                    line=node.start_point.row + 1,
                    column=node.start_point.column + 1,
                    code="MOD003",
                    message=(
                        f"duplicate {KIND_LABELS[kind]} name `{name}` "
                        f"defined in {module_list}"
                    ),
                    level="warning",
                )
            )

    return sorted(warnings, key=lambda violation: (str(violation.path), violation.line, violation.column))


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return upward-dependency and cycle violations in src/."""
    root = root.resolve()
    edges = collect_edges(root, config)
    violations = [
        edge_violation(
            edge,
            root,
            "MOD001",
            f"{format_module(edge.source)} must not depend only upward on strict ancestor {format_module(edge.target)}",
        )
        for edge in edges
        if is_strict_ancestor(edge.target, edge.source)
    ]

    for component in strongly_connected_components(edges):
        members = set(component)
        description = ", ".join(format_module(module) for module in component)

        violations.extend(
            edge_violation(
                edge,
                root,
                "MOD002",
                f"cyclic module dependency {format_module(edge.source)} -> "
                f"{format_module(edge.target)} in [{description}]",
            )
            for edge in edges
            if edge.source in members and edge.target in members
        )

    violations.extend(duplicate_name_warnings(root, config))

    return sorted(
        violations,
        key=lambda violation: (str(violation.path), violation.line, violation.column),
    )


def codes(violations: list[Violation]) -> list[str]:
    return [violation.code for violation in violations]


def write_fixture(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def valid_self_test(config: dict | None) -> list[Violation]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_fixture(root, "Cargo.toml", '[package]\nname = "fixture"\n')
        write_fixture(
            root,
            "src/lib.rs",
            "mod parent;\nmod port;\nmod part_impl;\n#[cfg(test)]\n"
            "// Tests remain outside the production module graph.\nmod tests;\n",
        )
        write_fixture(
            root,
            "src/parent.rs",
            "pub struct Owner;\npub mod child;\npub mod shared;\npub mod impls;\n"
            "use self::child::Child;\nfn child(_: Child) {}\n",
        )
        write_fixture(
            root,
            "src/parent/child.rs",
            "use super::shared::Helper;\npub struct Child;\nfn helper(_: Helper) {}\n",
        )
        write_fixture(root, "src/parent/shared.rs", "pub struct Helper;\n")
        write_fixture(
            root,
            "src/parent/impls.rs",
            "use super::Owner;\nuse crate::port::Trait;\nimpl Trait for Owner {}\n",
        )
        write_fixture(root, "src/port.rs", "pub trait Trait {}\n")
        write_fixture(root, "src/tests.rs", "use crate::*;\n")
        write_fixture(root, "src/orphan.rs", "use crate::*;\n")
        write_fixture(root, "src/part_impl.rs", "pub mod repo;\n")
        write_fixture(root, "src/part_impl/repo.rs", "pub mod rdb_impl;\n")
        write_fixture(
            root,
            "src/part_impl/repo/rdb_impl.rs",
            "pub mod schema;\npub mod user;\n",
        )
        write_fixture(
            root,
            "src/part_impl/repo/rdb_impl/user.rs",
            "use super::schema::Table;\nfn table(_: Table) {}\n",
        )
        write_fixture(
            root,
            "src/part_impl/repo/rdb_impl/schema.rs",
            "use crate::*;\npub struct Table;\n",
        )

        return check(root, config)


def invalid_direction_self_test(config: dict | None) -> list[Violation]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_fixture(root, "Cargo.toml", '[package]\nname = "fixture"\n')
        write_fixture(root, "src/lib.rs", "mod parent;\nmod port;\n")
        write_fixture(
            root,
            "src/parent.rs",
            "pub struct Owner;\npub mod child;\npub mod impls;\n"
            "mod inline { fn upward(_: super::Owner) {} }\n",
        )
        write_fixture(
            root,
            "src/parent/child.rs",
            "use super::Owner as ParentOwner;\n"
            "pub use super::Owner;\n"
            "use super::*;\n"
            "fn alias(_: ParentOwner) {}\n"
            "fn qualified(_: crate::parent::Owner) {}\n"
            "#[allow(crate::parent::lint)] struct Attributed;\n",
        )
        write_fixture(
            root,
            "src/parent/impls.rs",
            "use super::Owner;\nuse crate::port::Trait;\n"
            "impl Trait for Owner { fn use_again(&self) { let _: Option<Owner> = None; } }\n",
        )
        write_fixture(root, "src/port.rs", "pub trait Trait {}\n")

        return check(root, config)


def cycle_self_test(config: dict | None) -> list[Violation]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write_fixture(root, "Cargo.toml", '[package]\nname = "fixture"\n')
        write_fixture(root, "src/lib.rs", "mod a;\nmod b;\nmod c;\n")
        write_fixture(root, "src/a.rs", "use crate::b::B;\npub struct A(B);\n")
        write_fixture(root, "src/b.rs", "use crate::c::C;\npub struct B(C);\n")
        write_fixture(root, "src/c.rs", "use crate::a::A;\npub struct C(A);\n")

        return check(root, config)


def self_test() -> int:
    config = {"exclude_files": ["src/part_impl/repo/rdb_impl/schema.rs"]}
    valid_diagnostics = valid_self_test(config)

    if valid_diagnostics:
        print("self-test: valid dependency graph was rejected", file=sys.stderr)
        print("\n".join(str(violation) for violation in valid_diagnostics), file=sys.stderr)
        return 1

    direction_diagnostics = invalid_direction_self_test(config)

    if codes(direction_diagnostics).count("MOD001") < 7:
        print("self-test: upward path forms were not fully diagnosed", file=sys.stderr)
        print("\n".join(str(violation) for violation in direction_diagnostics), file=sys.stderr)
        return 1

    cycle_diagnostics = cycle_self_test(config)

    if codes(cycle_diagnostics).count("MOD002") != 3:
        print("self-test: multi-module cycle was not fully diagnosed", file=sys.stderr)
        print("\n".join(str(violation) for violation in cycle_diagnostics), file=sys.stderr)
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="module-dependency")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    violations = check(args.root.resolve())

    for violation in violations:
        prefix = "warning" if violation.level == "warning" else "error"
        print(f"{violation.path}:{violation.line}:{violation.column}: {violation.code}: {prefix}: {violation.message}", file=sys.stderr)

    return 1 if any(violation.level != "warning" for violation in violations) else 0


if __name__ == "__main__":
    raise SystemExit(main())
