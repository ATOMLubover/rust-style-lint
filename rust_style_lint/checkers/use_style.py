"""Enforce canonical module-scope Rust `use` declarations."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))
CATEGORIES = ("super", "std", "third_party", "local_crate", "crate")
KNOWN_TRAITS = {
    "anyhow::Context",
    "futures::FutureExt",
    "futures::StreamExt",
    "itertools::Itertools",
    "serde::Deserialize",
    "serde::Serialize",
    "std::convert::TryFrom",
    "std::convert::TryInto",
    "std::io::BufRead",
    "std::io::Read",
    "std::io::Write",
    "std::iter::Iterator",
    "tokio_stream::StreamExt",
    "tracing::Instrument",
}


@dataclass(frozen=True)
class Leaf:
    prefix: tuple[str, ...]
    leaf: str
    kind: str
    alias: str | None

    @property
    def full_path(self) -> str:
        return "::".join((*self.prefix, self.leaf))

    @property
    def identity(self) -> tuple[tuple[str, ...], str, str, str | None]:
        return self.prefix, self.leaf, self.kind, self.alias


@dataclass
class UseStmt:
    start: int
    end: int
    attr_start: int
    condition: tuple[str, ...]
    attr_signature: tuple[str, ...]
    indent: str
    scope_id: int
    public: bool
    in_tests_mod: bool
    requires_super_glob: bool
    leaves: list[Leaf]
    diagnostics: list[tuple[int, str, str]]


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


def module_scope(node: tree_sitter.Node) -> bool:
    return node.parent is not None and node.parent.type in {"source_file", "declaration_list"}


def is_public_use(node: tree_sitter.Node, source: bytes) -> bool:
    return text(source, node).lstrip().startswith("pub use ")


def leading_attributes(node: tree_sitter.Node) -> tuple[tree_sitter.Node, ...]:
    parent = node.parent

    if parent is None:
        return ()

    siblings = parent.named_children
    index = next(index for index, sibling in enumerate(siblings) if sibling.id == node.id)
    attrs: list[tree_sitter.Node] = []

    for sibling in reversed(siblings[:index]):
        if sibling.type == "attribute_item":
            attrs.append(sibling)
            continue

        if sibling.type in {"line_comment", "block_comment"}:
            continue

        break

    return tuple(reversed(attrs))


def normalized_cfg(source: bytes, attr: tree_sitter.Node) -> str | None:
    attribute = attr.named_child(0)

    if attribute is None or text(source, attribute.child_by_field_name("name") or attribute)[:3] != "cfg":
        return None

    value = text(source, attribute)

    if not value.startswith("cfg("):
        return None

    return re.sub(r"\s+", "", value)


def inside_tests_mod(node: tree_sitter.Node, source: bytes) -> bool:
    current = node.parent

    while current is not None:
        if current.type == "mod_item":
            name = current.child_by_field_name("name")

            if name is not None and text(source, name) == "tests":
                return True

        current = current.parent

    return False


def path_segments(source: bytes, node: tree_sitter.Node) -> tuple[str, ...]:
    return tuple(segment.strip() for segment in text(source, node).split("::"))


def direct_brace_item(node: tree_sitter.Node, source: bytes) -> bool:
    if node.type in {"identifier", "crate", "self", "super"}:
        return True

    if node.type == "use_wildcard":
        return text(source, node).strip() == "*"

    if node.type != "use_as_clause":
        return False

    target = node.named_children[0] if node.named_children else None
    return target is not None and target.type in {"identifier", "self"}


def leaf_from_path(segments: tuple[str, ...], alias: str | None = None) -> Leaf:
    if not segments:
        raise ValueError("empty use path")

    if segments[-1] == "*":
        return Leaf(segments[:-1], "*", "glob", alias)

    if segments[-1] == "self":
        return Leaf(segments[:-1], "self", "self", alias)

    return Leaf(segments[:-1], segments[-1], "name", alias)


def parse_tree(
    source: bytes,
    node: tree_sitter.Node,
    prefix: tuple[str, ...] = (),
) -> tuple[list[Leaf], list[tuple[int, str, str]]]:
    diagnostics: list[tuple[int, str, str]] = []

    if node.type == "scoped_use_list":
        path = node.child_by_field_name("path")
        use_list = node.child_by_field_name("list")

        if path is None or use_list is None:
            return [], [(node.start_byte, "USE_PARSE_ERROR", "invalid scoped use list")]

        return parse_tree(source, use_list, (*prefix, *path_segments(source, path)))

    if node.type == "use_list":
        leaves: list[Leaf] = []

        for child in node.named_children:
            if not direct_brace_item(child, source):
                diagnostics.append(
                    (
                        child.start_byte,
                        "USE_BRACE_NON_LEAF",
                        "brace items must be direct leaves, not nested paths",
                    ),
                )

            child_leaves, child_diagnostics = parse_tree(source, child, prefix)
            leaves.extend(child_leaves)
            diagnostics.extend(child_diagnostics)

        return leaves, diagnostics

    if node.type == "use_as_clause":
        children = node.named_children

        if len(children) != 2:
            return [], [(node.start_byte, "USE_PARSE_ERROR", "invalid use alias")]

        leaves, diagnostics = parse_tree(source, children[0], prefix)
        alias = text(source, children[1])
        return [replace(leaf, alias=alias) for leaf in leaves], diagnostics

    if node.type == "use_wildcard":
        value = text(source, node).strip()
        segments = prefix if value == "*" else (*prefix, *path_segments(source, node)[:-1])
        return [Leaf(segments, "*", "glob", None)], diagnostics

    if node.type in {"identifier", "crate", "self", "super", "scoped_identifier"}:
        return [leaf_from_path((*prefix, *path_segments(source, node)))], diagnostics

    return [], [(node.start_byte, "USE_PARSE_ERROR", f"unsupported use node {node.type}")]


def collect_uses(path: Path, source: bytes) -> list[UseStmt]:
    tree = PARSER.parse(source)
    statements: list[UseStmt] = []

    for node in descendants(tree.root_node, "use_declaration"):
        if not module_scope(node):
            continue

        argument = node.child_by_field_name("argument")

        if argument is None:
            continue

        attrs = leading_attributes(node)
        condition = tuple(filter(None, (normalized_cfg(source, attr) for attr in attrs)))
        leaves, diagnostics = parse_tree(source, argument)
        lexical_tests_mod = inside_tests_mod(node, source)
        test_file = path.name == "tests.rs" or "tests" in path.parts
        line_start = source.rfind(b"\n", 0, node.start_byte) + 1
        indent = source[line_start:node.start_byte].decode()
        attr_start = attrs[0].start_byte if attrs else node.start_byte
        statements.append(
            UseStmt(
                start=node.start_byte,
                end=node.end_byte,
                attr_start=attr_start,
                condition=condition,
                attr_signature=tuple(text(source, attr) for attr in attrs),
                indent=indent,
                scope_id=node.parent.id,
                public=is_public_use(node, source),
                in_tests_mod=lexical_tests_mod or test_file,
                requires_super_glob=lexical_tests_mod,
                leaves=leaves,
                diagnostics=diagnostics,
            ),
        )

    return sorted(statements, key=lambda statement: statement.start)


def workspace_crates(root: Path) -> set[str]:
    crates = {root.name.replace("-", "_")}

    for cargo_toml in (root / "Cargo.toml", *root.glob("*/Cargo.toml")):
        if not cargo_toml.exists():
            continue

        for match in re.finditer(r'^name\s*=\s*"([^"]+)"', cargo_toml.read_text(), re.MULTILINE):
            crates.add(match.group(1).replace("-", "_"))

    return crates


def category(leaf: Leaf, local_crates: set[str]) -> str:
    root = (leaf.prefix or (leaf.leaf,))[0]

    if root == "super":
        return "super"

    if root == "std":
        return "std"

    if root in {"crate", "self"}:
        return "crate"

    if root in local_crates:
        return "local_crate"

    return "third_party"


def canonicalize(leaves: list[Leaf]) -> list[Leaf]:
    prefixes = {leaf.prefix for leaf in leaves}
    normalized = []
    identities = set()

    for leaf in leaves:
        full = (*leaf.prefix, leaf.leaf)

        if leaf.kind == "name" and leaf.alias is None and full in prefixes:
            leaf = Leaf(full, "self", "self", None)

        if leaf.identity not in identities:
            normalized.append(leaf)
            identities.add(leaf.identity)

    return normalized


def explicit_trait_use(masked: bytes, name: str) -> bool:
    escaped = re.escape(name.encode())
    patterns = (
        rb"\bimpl\b[^;{}]*\b" + escaped + rb"\b[^;{}]*\bfor\b",
        rb"\bdyn\s+" + escaped + rb"\b",
        rb"<[^>]*\bas\s+" + escaped + rb"\b",
        rb"\bwhere\b[^{};]*:\s*[^{};]*\b" + escaped + rb"\b",
        rb"[:+<,]\s*" + escaped + rb"\b",
        rb"derive\s*\([^)]*\b" + escaped + rb"\b",
    )
    return any(re.search(pattern, masked, re.MULTILINE) for pattern in patterns)


def apply_trait_aliases(
    leaves: list[Leaf],
    masked: bytes,
    known_traits: set[str],
) -> tuple[list[Leaf], list[Leaf]]:
    changed: list[Leaf] = []
    fixed: list[Leaf] = []

    for leaf in leaves:
        if leaf.kind == "name" and leaf.full_path in known_traits and leaf.alias != "_":
            local_name = leaf.alias or leaf.leaf

            if not explicit_trait_use(masked, local_name):
                changed.append(leaf)
                leaf = replace(leaf, alias="_")

        fixed.append(leaf)

    return fixed, changed


def render_leaf(leaf: Leaf, public: bool) -> str:
    base = "::".join((*leaf.prefix, "*" if leaf.kind == "glob" else leaf.leaf))

    if leaf.kind == "self":
        base = "::".join(leaf.prefix)

    visibility = "pub " if public else ""
    return f"{visibility}use {base}{f' as {leaf.alias}' if leaf.alias else ''};"


def render_bucket(leaves: list[Leaf], public: bool) -> str:
    rank = {"self": 0, "name": 1, "glob": 2}
    leaves = sorted(leaves, key=lambda leaf: (rank[leaf.kind], leaf.leaf, leaf.alias or ""))

    if len(leaves) == 1:
        return render_leaf(leaves[0], public)

    prefix = "::".join(leaves[0].prefix)
    items = []

    for leaf in leaves:
        name = "self" if leaf.kind == "self" else "*" if leaf.kind == "glob" else leaf.leaf
        items.append(f"{name}{f' as {leaf.alias}' if leaf.alias else ''}")

    visibility = "pub " if public else ""
    return f"{visibility}use {prefix}::{{{', '.join(items)}}};"


def render_block(
    leaves: list[Leaf],
    local_crates: set[str],
    indent: str,
    public: bool,
) -> str:
    by_category: dict[str, list[Leaf]] = {category_name: [] for category_name in CATEGORIES}

    for leaf in canonicalize(leaves):
        by_category[category(leaf, local_crates)].append(leaf)

    lines: list[str] = []

    for category_name in CATEGORIES:
        buckets: dict[tuple[str, ...], list[Leaf]] = {}

        for leaf in by_category[category_name]:
            buckets.setdefault(leaf.prefix, []).append(leaf)

        group = [render_bucket(bucket, public) for bucket in buckets.values()]

        if group and lines:
            lines.append("")

        lines.extend(group)

    return ("\n" + indent).join(lines)


def contiguous_blocks(source: bytes, statements: list[UseStmt]) -> list[list[UseStmt]]:
    blocks: list[list[UseStmt]] = []
    current: list[UseStmt] = []

    for statement in statements:
        if current:
            previous = current[-1]
            gap = source[previous.end : statement.attr_start]
            separated = (
                previous.scope_id != statement.scope_id
                or previous.public != statement.public
                or gap.strip()
            )

            if separated:
                blocks.append(current)
                current = []

        current.append(statement)

    if current:
        blocks.append(current)

    return blocks


def condition_segments(block: list[UseStmt]) -> list[list[UseStmt]]:
    segments: list[list[UseStmt]] = []
    current: list[UseStmt] = []

    for statement in block:
        if current and (
            statement.condition != current[-1].condition
            or statement.attr_signature != current[-1].attr_signature
        ):
            segments.append(current)
            current = []

        current.append(statement)

    if current:
        segments.append(current)

    return segments


def scope_nodes(tree: tree_sitter.Tree) -> list[tree_sitter.Node]:
    return [tree.root_node, *descendants(tree.root_node, "declaration_list")]


def semantic_children(scope: tree_sitter.Node) -> list[tree_sitter.Node]:
    return [
        child
        for child in scope.named_children
        if child.type
        not in {"attribute_item", "inner_attribute_item", "block_comment", "line_comment"}
    ]


def item_start(source: bytes, node: tree_sitter.Node) -> int:
    parent = node.parent

    if parent is None:
        return node.start_byte

    siblings = parent.named_children
    index = next(index for index, sibling in enumerate(siblings) if sibling.id == node.id)
    start = node.start_byte

    for sibling in reversed(siblings[:index]):
        gap = source[sibling.end_byte : start]

        if gap.strip():
            break

        if sibling.type == "attribute_item":
            start = sibling.start_byte
            continue

        if sibling.type in {"line_comment", "block_comment"}:
            comment = text(source, sibling).lstrip()

            if not comment.startswith(("//!", "/*!")):
                start = sibling.start_byte
                continue

        break

    return source.rfind(b"\n", 0, start) + 1


def item_end(source: bytes, node: tree_sitter.Node) -> int:
    newline = source.find(b"\n", node.end_byte)
    return len(source) if newline < 0 else newline + 1


def item_rank(node: tree_sitter.Node, source: bytes) -> int:
    if node.type == "use_declaration":
        return 1 if is_public_use(node, source) else 0

    if node.type == "mod_item":
        return 2

    return 3


def check_item_order(path: Path, root: Path, source: bytes) -> list[Violation]:
    tree = PARSER.parse(source)
    violations: list[Violation] = []

    for scope in scope_nodes(tree):
        highest = -1

        for node in semantic_children(scope):
            rank = item_rank(node, source)

            if rank < highest:
                expected = "ordinary use, pub use, mod, then other code"
                violations.append(violation(path, root, source, node.start_byte, "USE_ITEM_ORDER", expected))

            highest = max(highest, rank)

    return violations


def check_use_block_boundaries(
    path: Path,
    root: Path,
    source: bytes,
    statements: list[UseStmt],
) -> list[Violation]:
    violations: list[Violation] = []
    scopes: dict[int, list[UseStmt]] = {}

    for statement in statements:
        scopes.setdefault(statement.scope_id, []).append(statement)

    for scope_statements in scopes.values():
        for previous, current in zip(scope_statements, scope_statements[1:]):
            if previous.public == current.public:
                continue

            gap = source[previous.end : current.attr_start]

            if gap.count(b"\n") != 2:
                violations.append(
                    violation(
                        path,
                        root,
                        source,
                        current.start,
                        "USE_VISIBILITY_BLANK_LINE",
                        "ordinary use and pub use blocks need exactly one blank line",
                    ),
                )

    return violations


def join_use_chunks(
    source: bytes,
    nodes: list[tree_sitter.Node],
    statements: dict[int, UseStmt],
    local_crates: set[str],
) -> bytes:
    chunks: list[bytes] = []

    for index, node in enumerate(nodes):
        start = item_start(source, node)
        chunk = source[start:item_end(source, node)].strip(b"\r\n")

        if index:
            previous = statements[nodes[index - 1].start_byte]
            current = statements[node.start_byte]
            previous_categories = {category(leaf, local_crates) for leaf in previous.leaves}
            current_categories = {category(leaf, local_crates) for leaf in current.leaves}
            same_condition = (
                previous.condition == current.condition
                and previous.attr_signature == current.attr_signature
            )
            separator = b"\n\n" if same_condition and previous_categories != current_categories else b"\n"
            chunks.append(separator)

        chunks.append(chunk)

    return b"".join(chunks)


def first_structure_edit(
    path: Path,
    source: bytes,
    local_crates: set[str],
) -> tuple[int, int, bytes] | None:
    tree = PARSER.parse(source)
    statements = {statement.start: statement for statement in collect_uses(path, source)}
    scopes = sorted(scope_nodes(tree), key=lambda scope: scope.end_byte - scope.start_byte)

    for scope in scopes:
        children = semantic_children(scope)
        ranks = [item_rank(node, source) for node in children]

        if ranks == sorted(ranks):
            continue

        declarations = [node for node in children if node.type in {"use_declaration", "mod_item"}]
        ordinary_uses = [
            node
            for node in declarations
            if node.type == "use_declaration" and not is_public_use(node, source)
        ]
        public_uses = [
            node
            for node in declarations
            if node.type == "use_declaration" and is_public_use(node, source)
        ]
        modules = [node for node in declarations if node.type == "mod_item"]
        sections = [
            join_use_chunks(source, ordinary_uses, statements, local_crates),
            join_use_chunks(source, public_uses, statements, local_crates),
            b"\n".join(
                source[item_start(source, node):item_end(source, node)].strip(b"\r\n")
                for node in modules
            ),
        ]
        header = b"\n\n".join(section for section in sections if section) + b"\n\n"
        anchor = item_start(source, children[0])
        edits: list[tuple[int, int, bytes]] = []
        replaced_anchor = False

        for node in declarations:
            start = item_start(source, node)
            end = item_end(source, node)

            if start == anchor:
                edits.append((start, end, header))
                replaced_anchor = True
            else:
                edits.append((start, end, b""))

        if not replaced_anchor:
            edits.append((anchor, anchor, header))

        updated = source

        for start, end, replacement in sorted(edits, reverse=True):
            updated = updated[:start] + replacement + updated[end:]

        return 0, len(source), updated.rstrip() + b"\n"

    return None


def violation(
    path: Path,
    root: Path,
    source: bytes,
    index: int,
    code: str,
    message: str,
) -> Violation:
    return Violation(
        path=path.relative_to(root),
        line=source.count(b"\n", 0, index) + 1,
        code=code,
        message=message,
    )


def masked_source(source: bytes, statements: list[UseStmt]) -> bytes:
    masked = bytearray(source)

    for statement in statements:
        for index in range(statement.start, statement.end):
            if masked[index] not in (10, 13):
                masked[index] = 32

    return bytes(masked)


def check_test_super_imports(
    path: Path,
    root: Path,
    source: bytes,
    statements: list[UseStmt],
) -> list[Violation]:
    scopes: dict[int, list[UseStmt]] = {}

    for statement in statements:
        if statement.requires_super_glob:
            scopes.setdefault(statement.scope_id, []).append(statement)

    violations: list[Violation] = []

    for scope_statements in scopes.values():
        super_statements = [
            statement
            for statement in scope_statements
            if any(category(leaf, set()) == "super" for leaf in statement.leaves)
        ]

        if not super_statements:
            violations.append(violation(path, root, source, scope_statements[0].start, "USE_SUPER_IN_TESTS_MISSING", "mod tests must contain exactly one `use super::*;`"))
            continue

        for statement in super_statements:
            pure_glob = (
                not statement.public
                and len(statement.leaves) == 1
                and statement.leaves[0] == Leaf(("super",), "*", "glob", None)
            )

            if not pure_glob:
                violations.append(violation(path, root, source, statement.start, "USE_SUPER_IN_TESTS_NOT_GLOB", "super imports in mod tests must be `use super::*;`"))

        for statement in super_statements[1:]:
            violations.append(violation(path, root, source, statement.start, "USE_SUPER_IN_TESTS_TOO_MANY", "only one `use super::*;` is allowed in mod tests"))

    return violations


def check_single_test_module(
    path: Path,
    root: Path,
    source: bytes,
) -> list[Violation]:
    """Each file may have at most one #[cfg(test)] mod declaration."""
    tree = PARSER.parse(source)
    violations: list[Violation] = []
    test_mod_nodes: list[tree_sitter.Node] = []

    for node in descendants(tree.root_node, "mod_item"):
        for attr in leading_attributes(node):
            condition = normalized_cfg(source, attr)

            if condition == "cfg(test)":
                test_mod_nodes.append(node)
                break

    if len(test_mod_nodes) > 1:
        for node in test_mod_nodes[1:]:
            violations.append(
                violation(
                    path,
                    root,
                    source,
                    node.start_byte,
                    "USE_MULTIPLE_TEST_MODS",
                    "only one #[cfg(test)] mod declaration is allowed per file; merge all tests into mod tests",
                ),
            )

    return violations


def check_file(
    path: Path,
    root: Path,
    local_crates: set[str],
    known_traits: set[str] = KNOWN_TRAITS,
) -> tuple[list[Violation], list[tuple[int, int, bytes]]]:
    source = production_source(path, root)
    statements = collect_uses(path, source)
    masked = masked_source(source, statements)
    violations: list[Violation] = []
    edits: list[tuple[int, int, bytes]] = []

    for statement in statements:
        for index, code, message in statement.diagnostics:
            violations.append(violation(path, root, source, index, code, message))

        for leaf in statement.leaves:
            if category(leaf, local_crates) == "super" and not statement.in_tests_mod:
                violations.append(violation(path, root, source, statement.start, "USE_SUPER_OUTSIDE_TESTS", "`super` imports are only allowed inside mod tests"))

    violations.extend(check_test_super_imports(path, root, source, statements))
    violations.extend(check_use_block_boundaries(path, root, source, statements))
    violations.extend(check_item_order(path, root, source))
    violations.extend(check_single_test_module(path, root, source))

    for block in contiguous_blocks(source, statements):
        for segment in condition_segments(block):
            leaves = [leaf for statement in segment for leaf in statement.leaves]
            aliased_leaves, missing_aliases = apply_trait_aliases(leaves, masked, known_traits)

            for leaf in missing_aliases:
                violations.append(violation(path, root, source, segment[0].start, "USE_TRAIT_ALIAS_MISSING", f"trait import `{leaf.full_path}` should use `as _`"))

            categories = [
                {category(leaf, local_crates) for leaf in statement.leaves}
                for statement in segment
            ]
            last_category = -1

            for statement, statement_categories in zip(segment, categories, strict=True):
                if len(statement_categories) > 1:
                    violations.append(violation(path, root, source, statement.start, "USE_MIXED_GROUP", "one use tree must not mix import groups"))

                if statement_categories:
                    category_index = max(CATEGORIES.index(value) for value in statement_categories)

                    if category_index < last_category:
                        violations.append(violation(path, root, source, statement.start, "USE_GROUP_ORDER", "use group appears after a later group"))

                    last_category = max(last_category, category_index)

            for previous, current in zip(segment, segment[1:]):
                previous_categories = {category(leaf, local_crates) for leaf in previous.leaves}
                current_categories = {category(leaf, local_crates) for leaf in current.leaves}

                if previous_categories and current_categories and previous_categories != current_categories:
                    gap = source[previous.end : current.attr_start]

                    if gap.count(b"\n") != 2:
                        violations.append(violation(path, root, source, current.start, "USE_GROUP_BLANK_LINE", "different use groups need exactly one blank line"))

            canonical = canonicalize(aliased_leaves)
            raw_identities = [leaf.identity for leaf in aliased_leaves]

            if len(set(raw_identities)) != len(raw_identities):
                violations.append(violation(path, root, source, segment[0].start, "USE_DUPLICATE_IMPORT", "duplicate imports must be merged"))

            buckets: dict[tuple[str, ...], list[Leaf]] = {}

            for leaf in canonical:
                buckets.setdefault(leaf.prefix, []).append(leaf)

            for bucket in buckets.values():
                if len(bucket) < 2:
                    continue

                expected = {leaf.identity for leaf in bucket}

                if not any({leaf.identity for leaf in canonicalize(statement.leaves)} == expected for statement in segment):
                    violations.append(violation(path, root, source, segment[0].start, "USE_MISSING_MERGE", f"imports under `{'::'.join(bucket[0].prefix)}` must share one use tree"))

            if any(
                diagnostic[1] == "USE_PARSE_ERROR"
                for statement in segment
                for diagnostic in statement.diagnostics
            ):
                continue

            fixed_leaves, _ = apply_trait_aliases(leaves, masked, known_traits)
            rendered = render_block(
                fixed_leaves,
                local_crates,
                segment[0].indent,
                segment[0].public,
            )
            first = segment[0]
            prefix = source[first.attr_start : first.start]
            replacement = prefix + rendered.encode()
            edits.append((first.attr_start, segment[-1].end, replacement))

    return violations, edits


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


def apply_fixes(path: Path, edits: list[tuple[int, int, bytes]]) -> None:
    source = path.read_bytes()

    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]

    path.write_bytes(source)


def apply_structure_fixes(path: Path, local_crates: set[str]) -> None:
    for _ in range(100):
        source = path.read_bytes()
        edit = first_structure_edit(path, source, local_crates)

        if edit is None:
            return

        apply_fixes(path, [edit])

    raise RuntimeError(f"structure fixer did not converge for {path}")


def fix_file(path: Path, root: Path, local_crates: set[str], known_traits: set[str] = KNOWN_TRAITS) -> list[Violation]:
    for _ in range(100):
        before = path.read_bytes()
        _, edits = check_file(path, root, local_crates, known_traits)
        apply_fixes(path, edits)
        apply_structure_fixes(path, local_crates)

        if path.read_bytes() == before:
            errors, _ = check_file(path, root, local_crates, known_traits)
            return errors

    raise RuntimeError(f"use-style fixer did not converge for {path}")


def configured_traits(config: dict | None) -> set[str]:
    if config is None:
        return KNOWN_TRAITS

    return KNOWN_TRAITS | {str(trait) for trait in config.get("traits", [])}


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return use-style violations under src/."""
    root = root.resolve()
    local_crates = workspace_crates(root)
    known_traits = configured_traits(config)
    diagnostics: list[Violation] = []

    for path in rust_files(root, []):
        errors, _ = check_file(path, root, local_crates, known_traits)
        diagnostics.extend(errors)

    return diagnostics


def fix(root: Path, config: dict | None = None) -> list[Violation]:
    """Rewrite use declarations, then format them with cargo fmt."""
    root = root.resolve()
    local_crates = workspace_crates(root)
    known_traits = configured_traits(config)

    for path in rust_files(root, []):
        fix_file(path, root, local_crates, known_traits)

    subprocess.run(["cargo", "fmt", "--all"], cwd=root, check=True)

    return check(root, config)


def self_test() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fixture = root / "src" / "lib.rs"
        fixture.parent.mkdir(parents=True)
        fixture.write_text(
            "#[cfg(feature = \"a\")]\n"
            "use crate::a::A;\n"
            "#[cfg(not(feature = \"a\"))]\n"
            "use crate::a::B;\n"
            "#[cfg(unix)]\n"
            "use std::io::Write;\n"
            "\n"
            "pub use crate::reexport::Thing;\n\n"
            "fn local() { use std::mem::take; }\n",
        )
        diagnostics, _ = check_file(fixture, root, {root.name})

        if any(violation.code in {"USE_GROUP_ORDER", "USE_MISSING_MERGE"} for violation in diagnostics):
            print("self-test: cfg conditions were merged into one import group", file=sys.stderr)
            return 1

        if not any(violation.code == "USE_TRAIT_ALIAS_MISSING" for violation in diagnostics):
            print("self-test: known trait import was not checked", file=sys.stderr)
            return 1

        fixture.write_text(
            "#[cfg(feature = \"a\")]\n"
            "use std::{mem::take, time};\n"
            "#[cfg(feature = \"a\")]\n"
            "use std::mem::take;\n"
            "#[cfg(not(feature = \"a\"))]\n"
            "use crate::{b::B, a::A};\n",
        )
        _, edits = check_file(fixture, root, {root.name})
        apply_fixes(fixture, edits)
        fixed = fixture.read_text()

        if "use std::{mem::take, time};" in fixed or fixed.count("#[cfg(feature = \"a\")]") != 1:
            print("self-test: cfg-safe use fix was not applied", file=sys.stderr)
            return 1

        diagnostics, _ = check_file(fixture, root, {root.name})

        if diagnostics:
            print("self-test: fixed source still has diagnostics", file=sys.stderr)
            print("\n".join(str(violation) for violation in diagnostics), file=sys.stderr)
            return 1

        fixture.write_text("mod tests {\n    use super::*;\n}\n")
        diagnostics, _ = check_file(fixture, root, {root.name})

        if diagnostics:
            print("self-test: valid test-module super import was rejected", file=sys.stderr)
            return 1

        fixture.write_text("mod tests {\n    use super::helper;\n}\n")
        diagnostics, _ = check_file(fixture, root, {root.name})

        if not any(violation.code == "USE_SUPER_IN_TESTS_NOT_GLOB" for violation in diagnostics):
            print("self-test: invalid test-module super import was accepted", file=sys.stderr)
            return 1

        fixture.write_text(
            "mod before;\n"
            "use std::time::Duration;\n"
            "pub use crate::api::Api;\n"
            "fn main_code() {}\n"
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    use super::*;\n"
            "}\n",
        )
        diagnostics, edits = check_file(fixture, root, {root.name})

        if not any(violation.code == "USE_ITEM_ORDER" for violation in diagnostics):
            print("self-test: invalid item order was accepted", file=sys.stderr)
            return 1

        apply_fixes(fixture, edits)
        apply_structure_fixes(fixture, {root.name})
        fixed = fixture.read_text()

        if fixed.index("use std") > fixed.index("pub use") or fixed.index("pub use") > fixed.index("mod before"):
            print("self-test: use/pub use/mod order was not fixed", file=sys.stderr)
            return 1

        if fixed.index("mod tests") > fixed.index("fn main_code"):
            print("self-test: trailing test module was not moved", file=sys.stderr)
            return 1

        diagnostics, _ = check_file(fixture, root, {root.name})

        if diagnostics:
            print("self-test: structure-fixed source still has diagnostics", file=sys.stderr)
            print("\n".join(str(violation) for violation in diagnostics), file=sys.stderr)
            return 1

        fixture.write_text(
            "mod outer {\n"
            "    fn code() {}\n"
            "\n"
            "    // Keep this comment with the test module.\n"
            "    #[cfg(test)]\n"
            "    mod tests {\n"
            "        use super::*;\n"
            "    }\n"
            "}\n",
        )
        apply_structure_fixes(fixture, {root.name})
        fixed = fixture.read_text()

        if fixed.index("// Keep this comment") > fixed.index("fn code"):
            print("self-test: module comment was not moved with its declaration", file=sys.stderr)
            return 1

        diagnostics, _ = check_file(fixture, root, {root.name})

        if diagnostics:
            print("self-test: nested structure-fixed source still has diagnostics", file=sys.stderr)
            print("\n".join(str(violation) for violation in diagnostics), file=sys.stderr)
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="use-style")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--fix",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="rewrite use declarations, then format them with cargo fmt",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = args.root.resolve()
    local_crates = workspace_crates(root)
    diagnostics: list[Violation] = []

    paths = rust_files(root, args.paths)

    if args.fix:
        for path in paths:
            fix_file(path, root, local_crates)

        subprocess.run(["cargo", "fmt", "--all"], cwd=root, check=True)

    for path in paths:
        errors, _ = check_file(path, root, local_crates)
        diagnostics.extend(errors)

    for violation in diagnostics:
        print(f"{violation.path}:{violation.line}: {violation.code}: {violation.message}", file=sys.stderr)

    return 1 if diagnostics else 0


if __name__ == "__main__":
    raise SystemExit(main())
