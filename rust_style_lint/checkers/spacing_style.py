"""Enforce custom Rust block-spacing rules: block-start separators, blank
lines between direct statements, match arms, and enum variants."""

from __future__ import annotations

import argparse
import bisect
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from tree_sitter import Language, Node, Parser
import tree_sitter_rust

from ..base import Violation
from ..config import merged
from ..production_source import production_source


COMMENT_NODE_TYPES = {
    "line_comment",
    "block_comment",
}
BLOCK_CONTAINERS = {
    "block",
    "match_block",
}
STRUCT_FIELD_CONTAINERS = {
    "field_declaration_list",
    "field_initializer_list",
}
ENUM_VARIANT_CONTAINERS = {
    "enum_variant_list",
}
# Module-scope item containers: the file root and the declaration_list body of
# an inline `mod`. impl/trait/extern method bodies are deliberately excluded —
# item spacing is a module-scope rule.
ITEM_CONTAINER_TYPES = {
    "source_file",
    "declaration_list",
}
# Every item that counts as a "big block" needing a blank line around it.
ITEM_TYPES = {
    "use_declaration",
    "function_item",
    "struct_item",
    "enum_item",
    "union_item",
    "impl_item",
    "trait_item",
    "mod_item",
    "const_item",
    "static_item",
    "type_item",
    "macro_definition",
    "macro_invocation",
    "expression_statement",
    "extern_crate_declaration",
    "foreign_mod_item",
    "function_signature_item",
    "associated_type",
}
# Non-item nodes that attach to the following item: outer attributes and
# comments stay glued to the item they document, so a missing blank line is
# reported before the whole group.
ITEM_ATTACH_TYPES = {
    "attribute_item",
    "inner_attribute_item",
    "line_comment",
    "block_comment",
}
# Consecutive same-type header items are grouped (use-style owns their
# internal spacing), so no blank line is forced between them.
HEADER_ITEM_TYPES = {
    "use_declaration",
    "mod_item",
}

BARE_SEPARATOR_RE = re.compile(r"^\s*//\s*$")


@dataclass(frozen=True)
class TextEdit:
    start_byte: int
    end_byte: int
    replacement: bytes
    code: str


@dataclass(frozen=True)
class FileAnalysis:
    diagnostics: tuple[Violation, ...]
    edits: tuple[TextEdit, ...]
    has_parse_errors: bool


@dataclass(frozen=True)
class MappedPoint:
    row: int
    column: int


@dataclass(frozen=True)
class MappedNode:
    """A node from a macro-fragment re-parse whose positions were remapped to
    the original file. Exposes only the attributes the spacing helpers read."""

    type: str
    start_point: MappedPoint
    end_point: MappedPoint
    start_byte: int
    end_byte: int


@dataclass(frozen=True)
class ContainerView:
    """A block/match_block shaped object fed to `_analyze_container` for
    macro-body content, mirroring the node attributes that helper reads."""

    type: str
    named_children: tuple[MappedNode, ...]
    children: tuple[object, ...]


class RustSpacingChecker:
    def __init__(self, root: Path) -> None:
        language = Language(tree_sitter_rust.language())
        self.parser = Parser(language)
        self.root = root.resolve()

    def analyze_file(
        self,
        path: Path,
        *,
        build_fixes: bool = False,
    ) -> FileAnalysis:
        source = production_source(path, self.root)

        return self.analyze_source(
            path,
            source,
            build_fixes=build_fixes,
        )

    def analyze_source(
        self,
        path: Path,
        source: bytes,
        *,
        build_fixes: bool = False,
    ) -> FileAnalysis:
        text = source.decode("utf-8")
        lines = text.splitlines()
        line_starts = byte_line_starts(source)
        newline = detect_newline(source)

        tree = self.parser.parse(source)

        # Collect nodes immediately to avoid tree-sitter Node wrapper
        # lifetime issues when generators are iterated interleaved with GC.
        nodes = iter_nodes(tree.root_node)

        parse_diagnostics = self._parse_error_violations(path, nodes)
        diagnostics = list(parse_diagnostics)
        edits: list[TextEdit] = []

        for container in nodes:
            if container.type not in (
                BLOCK_CONTAINERS
                | STRUCT_FIELD_CONTAINERS
                | ENUM_VARIANT_CONTAINERS
            ):
                continue

            container_diagnostics, container_edits = self._analyze_container(
                path=path,
                source=source,
                lines=lines,
                line_starts=line_starts,
                newline=newline,
                container=container,
                build_fixes=build_fixes,
            )

            diagnostics.extend(container_diagnostics)
            edits.extend(container_edits)

        # Item-level spacing: module-scope items (top level and nested mod
        # bodies) must be separated by a blank line.
        for container in nodes:
            if not is_item_container(container):
                continue

            item_diagnostics, item_edits = self._analyze_item_container(
                path=path,
                lines=lines,
                line_starts=line_starts,
                newline=newline,
                container=container,
                build_fixes=build_fixes,
            )

            diagnostics.extend(item_diagnostics)
            edits.extend(item_edits)

        # Macro bodies are opaque token trees to tree-sitter, so no
        # block/match_block nodes exist inside them. Re-parse each macro
        # body fragment that is valid Rust (arm bodies, nested matches, …)
        # and apply the same rules, warning-only; fall back to a `=>`
        # match-like heuristic for custom fragments (select! arm lists).
        diagnostics.extend(
            self._analyze_macro_invocations(
                path=path,
                source=source,
                lines=lines,
                line_starts=line_starts,
                newline=newline,
                nodes=nodes,
            )
        )

        unique_diagnostics = sorted(
            set(diagnostics),
            key=lambda diagnostic: (
                str(diagnostic.path),
                diagnostic.line,
                diagnostic.column,
                diagnostic.code,
            ),
        )

        unique_edits = normalize_edits(edits)

        return FileAnalysis(
            diagnostics=tuple(unique_diagnostics),
            edits=tuple(unique_edits),
            has_parse_errors=bool(parse_diagnostics),
        )

    def _relative(self, path: Path) -> Path:
        return path.relative_to(self.root) if path.is_absolute() else path

    def _analyze_container(
        self,
        *,
        path: Path,
        source: bytes,
        lines: list[str],
        line_starts: list[int],
        newline: bytes,
        container: Node,
        build_fixes: bool,
    ) -> tuple[list[Violation], list[TextEdit]]:
        units = direct_units(container)

        if not units:
            return [], []

        brace = opening_brace(container)

        if brace is None:
            return [], []

        diagnostics: list[Violation] = []
        edits: list[TextEdit] = []

        first = unit_anchor(container, units[0])

        separator_rows = separator_rows_before_first(
            lines=lines,
            brace=brace,
            first=first,
        )
        comment_rows = comment_rows_before_first(
            lines=lines,
            brace=brace,
            first=first,
        )

        # Blocks whose content needs visual separation:
        #
        # if condition {
        #     //
        #     statement_1;
        #
        #     statement_2;
        # }
        #
        # The separator exists because multi-line content is hard to scan:
        # it is required for every multi-unit block AND for a single unit
        # that itself spans several lines (e.g. a long `match` statement).
        # A `{` alone on its line is exempt; a compact single-line unit is
        # exempt. A real comment before the first unit also separates
        # visually, so it satisfies the rule. Struct declarations and
        # struct literals never use the separator — their field lists are
        # self-delimiting.
        first_unit_span_lines = units[0].start_point.row < units[0].end_point.row
        needs_separator = len(units) >= 2 or first_unit_span_lines

        if (
            needs_separator
            and container.type in BLOCK_CONTAINERS
            and not line_is_only_open_brace(lines, brace)
            and not separator_rows
            and not comment_rows
        ):
            kind = unit_kind(container)
            description = (
                f"multi-{kind} block"
                if len(units) >= 2
                else f"multi-line {kind} block"
            )

            diagnostics.append(
                Violation(
                    path=self._relative(path),
                    line=first.start_point.row + 1,
                    column=first.start_point.column + 1,
                    code="BLK000",
                    message=(
                        f"{description} whose opening brace is not on its "
                        f"own line requires a bare `//` separator before its "
                        f"first {kind}"
                    ),
                ),
            )

            if build_fixes:
                edit = build_block_start_separator_edit(
                    source=source,
                    lines=lines,
                    line_starts=line_starts,
                    newline=newline,
                    brace=brace,
                    first=first,
                )

                if edit is not None:
                    edits.append(edit)

        # Struct declarations and struct literals must not carry a bare `//`
        # separator: their field lists are self-delimiting and any separator
        # between `{` and the first field is noise.
        #
        # pub struct UserMessageBody {
        #     //
        #     pub content: String,
        # }
        if (
            container.type in STRUCT_FIELD_CONTAINERS
            and separator_rows
        ):
            diagnostics.append(
                Violation(
                    path=self._relative(path),
                    line=separator_rows[0] + 1,
                    column=1,
                    code="BLK002",
                    message=(
                        "bare `//` separator is forbidden in struct "
                        "declarations and struct literals; the field list "
                        "needs no separator"
                    ),
                ),
            )

            if build_fixes:
                edits.extend(
                    build_redundant_separator_edits(
                        lines=lines,
                        line_starts=line_starts,
                        brace=brace,
                        first=first,
                        separator_rows=separator_rows,
                    )
                )

        # Remove separators from compact blocks: a single unit that fits on
        # one line needs no bare `//` before it.
        #
        # if condition {
        #     //
        #     return;
        # }
        if (
            len(units) == 1
            and not first_unit_span_lines
            and container.type in BLOCK_CONTAINERS
            and separator_rows
        ):
            diagnostics.append(
                Violation(
                    path=self._relative(path),
                    line=separator_rows[0] + 1,
                    column=1,
                    code="BLK002",
                    message=(
                        "bare `//` block-start separator is redundant in a "
                        "single-statement block"
                    ),
                ),
            )

            if build_fixes:
                edits.extend(
                    build_redundant_separator_edits(
                        lines=lines,
                        line_starts=line_starts,
                        brace=brace,
                        first=first,
                        separator_rows=separator_rows,
                    )
                )

        # Direct statements, match arms, and enum variants must be
        # separated by a blank line.
        for previous, current in zip(units, units[1:]):
            if container.type not in (BLOCK_CONTAINERS | ENUM_VARIANT_CONTAINERS):
                continue

            current_anchor = unit_anchor(container, current)

            # Two units on the same line (e.g. separated by `;`) need no blank line.
            if previous.end_point.row == current_anchor.start_point.row:
                continue

            if has_blank_line_between(lines, previous, current_anchor):
                continue

            kind = unit_kind(container)

            diagnostics.append(
                Violation(
                    path=self._relative(path),
                    line=current_anchor.start_point.row + 1,
                    column=current_anchor.start_point.column + 1,
                    code="BLK001",
                    message=(
                        f"missing blank line before this {kind}; previous "
                        f"{kind} ended at line {previous.end_point.row + 1}"
                    ),
                ),
            )

            if build_fixes:
                edit = build_blank_line_edit(
                    source=source,
                    line_starts=line_starts,
                    newline=newline,
                    container=container,
                    previous=previous,
                    current=current_anchor,
                )

                if edit is not None:
                    edits.append(edit)

        return diagnostics, edits

    def _analyze_item_container(
        self,
        *,
        path: Path,
        lines: list[str],
        line_starts: list[int],
        newline: bytes,
        container: Node,
        build_fixes: bool,
    ) -> tuple[list[Violation], list[TextEdit]]:
        items = [
            child
            for child in container.named_children
            if child.type in ITEM_TYPES
        ]

        if len(items) < 2:
            return [], []

        diagnostics: list[Violation] = []
        edits: list[TextEdit] = []

        for index, current in enumerate(items[1:], start=1):
            previous = items[index - 1]

            # Consecutive use/mod declarations stay grouped; use-style owns
            # their internal blank-line rules.
            if previous.type == current.type and previous.type in HEADER_ITEM_TYPES:
                continue

            anchor = item_anchor(container, current)

            # Two items on the same line (e.g. split by `;`) need no blank line.
            if previous.end_point.row == anchor.start_point.row:
                continue

            if has_blank_line_between(lines, previous, anchor):
                continue

            diagnostics.append(
                Violation(
                    path=self._relative(path),
                    line=anchor.start_point.row + 1,
                    column=anchor.start_point.column + 1,
                    code="BLK003",
                    message=(
                        f"missing blank line before this {item_kind(current)}; "
                        f"previous {item_kind(previous)} ended at line "
                        f"{previous.end_point.row + 1}"
                    ),
                ),
            )

            if build_fixes:
                edit = build_item_blank_line_edit(
                    line_starts=line_starts,
                    newline=newline,
                    anchor=anchor,
                )

                if edit is not None:
                    edits.append(edit)

        return diagnostics, edits

    def _parse_error_violations(
        self,
        path: Path,
        nodes: list[Node],
    ) -> list[Violation]:
        relative = path.relative_to(self.root) if path.is_absolute() else path
        violations: list[Violation] = []

        for node in nodes:
            if node.type != "ERROR" and not node.is_missing:
                continue

            violations.append(
                Violation(
                    path=relative,
                    line=node.start_point.row + 1,
                    column=node.start_point.column + 1,
                    code="PARSE001",
                    message=(
                        f"Rust syntax tree contains {node.type!r}; spacing "
                        "results near this location may be incomplete"
                    ),
                ),
            )

        return violations

    def _point_for(self, line_starts: list[int], byte: int) -> MappedPoint:
        row = bisect.bisect_right(line_starts, byte) - 1
        return MappedPoint(row=row, column=byte - line_starts[row] + 1)

    def _mapped_node(
        self,
        line_starts: list[int],
        base_byte: int,
        node: Node,
        node_type: str | None = None,
    ) -> MappedNode:
        start_byte = base_byte + node.start_byte
        end_byte = base_byte + node.end_byte

        return MappedNode(
            type=node_type or node.type,
            start_point=self._point_for(line_starts, start_byte),
            end_point=self._point_for(line_starts, end_byte),
            start_byte=start_byte,
            end_byte=end_byte,
        )

    @staticmethod
    def _macro_violation(violation: Violation) -> Violation:
        return replace(
            violation,
            level="warning",
            message=f"{violation.message} (macro body)",
        )

    def _analyze_macro_invocations(
        self,
        *,
        path: Path,
        source: bytes,
        lines: list[str],
        line_starts: list[int],
        newline: bytes,
        nodes: list[Node],
    ) -> list[Violation]:
        diagnostics: list[Violation] = []

        for node in nodes:
            if node.type != "macro_invocation":
                continue

            tt = brace_body(node)

            if tt is not None:
                diagnostics.extend(
                    self._analyze_braced(
                        tt=tt,
                        tt_base=0,
                        path=path,
                        source=source,
                        lines=lines,
                        line_starts=line_starts,
                        newline=newline,
                    )
                )

        return diagnostics

    def _analyze_braced(
        self,
        *,
        tt: Node,
        tt_base: int,
        path: Path,
        source: bytes,
        lines: list[str],
        line_starts: list[int],
        newline: bytes,
    ) -> list[Violation]:
        open_brace = tt.children[0]
        close_brace = tt.children[-1]
        inner_start = tt_base + open_brace.end_byte
        inner_end = tt_base + close_brace.start_byte
        inner = source[inner_start:inner_end]
        sub = self.parser.parse(inner)
        sub_nodes = iter_nodes(sub.root_node)

        # The fragment is not valid Rust (select! arm list, bare match arms,
        # custom DSL): fall back to the `=>` match-like heuristic.
        if any(node.type == "ERROR" for node in sub_nodes):
            return self._analyze_custom_body(
                tt=tt,
                tt_base=tt_base,
                path=path,
                source=source,
                lines=lines,
                line_starts=line_starts,
                newline=newline,
            )

        diagnostics: list[Violation] = []

        # The token_tree itself is a block whose units are the fragment's
        # top-level statements (or the single match expression).
        diagnostics.extend(
            self._analyze_container_as_block(
                tt=tt,
                tt_base=tt_base,
                sub=sub,
                base_byte=inner_start,
                path=path,
                source=source,
                lines=lines,
                line_starts=line_starts,
                newline=newline,
            )
        )

        # Real nested containers (else blocks, inner match blocks, …) inside
        # the fragment get the same treatment as top-level ones.
        for node in sub_nodes:
            if node.type not in (
                BLOCK_CONTAINERS | STRUCT_FIELD_CONTAINERS | ENUM_VARIANT_CONTAINERS
            ):
                continue

            diagnostics.extend(
                self._macro_violation(violation)
                for violation in self._analyze_container_from_sub_node(
                    node=node,
                    base_byte=inner_start,
                    path=path,
                    source=source,
                    lines=lines,
                    line_starts=line_starts,
                    newline=newline,
                )
            )

        # Nested macro invocations inside the fragment recurse.
        for node in sub_nodes:
            if node.type != "macro_invocation":
                continue

            nested_tt = brace_body(node)

            if nested_tt is not None:
                diagnostics.extend(
                    self._analyze_braced(
                        tt=nested_tt,
                        tt_base=inner_start,
                        path=path,
                        source=source,
                        lines=lines,
                        line_starts=line_starts,
                        newline=newline,
                    )
                )

        return diagnostics

    def _analyze_container_as_block(
        self,
        *,
        tt: Node,
        tt_base: int,
        sub,
        base_byte: int,
        path: Path,
        source: bytes,
        lines: list[str],
        line_starts: list[int],
        newline: bytes,
    ) -> list[Violation]:
        members = tuple(
            self._mapped_node(line_starts, base_byte, child)
            for child in sub.root_node.named_children
        )

        if not members:
            return []

        brace = self._mapped_node(line_starts, tt_base, tt.children[0], node_type="{")
        close = self._mapped_node(line_starts, tt_base, tt.children[-1], node_type="}")
        container = ContainerView(
            type="block",
            named_children=members,
            children=(brace, *members, close),
        )

        diagnostics, _ = self._analyze_container(
            path=path,
            source=source,
            lines=lines,
            line_starts=line_starts,
            newline=newline,
            container=container,
            build_fixes=False,
        )

        return [self._macro_violation(violation) for violation in diagnostics]

    def _analyze_container_from_sub_node(
        self,
        *,
        node: Node,
        base_byte: int,
        path: Path,
        source: bytes,
        lines: list[str],
        line_starts: list[int],
        newline: bytes,
    ) -> list[Violation]:
        units = tuple(
            self._mapped_node(line_starts, base_byte, unit)
            for unit in direct_units(node)
        )

        if not units:
            return []

        brace_node = opening_brace(node)

        if brace_node is None:
            return []

        brace = self._mapped_node(line_starts, base_byte, brace_node, node_type="{")
        close = self._mapped_node(line_starts, base_byte, node.children[-1], node_type="}")
        container = ContainerView(
            type=node.type,
            named_children=units,
            children=(brace, *units, close),
        )

        diagnostics, _ = self._analyze_container(
            path=path,
            source=source,
            lines=lines,
            line_starts=line_starts,
            newline=newline,
            container=container,
            build_fixes=False,
        )

        return diagnostics

    def _analyze_custom_body(
        self,
        *,
        tt: Node,
        tt_base: int,
        path: Path,
        source: bytes,
        lines: list[str],
        line_starts: list[int],
        newline: bytes,
    ) -> list[Violation]:
        diagnostics: list[Violation] = []
        arms = match_like_arms(tt)

        if len(arms) >= 2:
            units = tuple(
                MappedNode(
                    type="match_arm",
                    start_point=self._point_for(line_starts, tt_base + start.start_byte),
                    end_point=self._point_for(line_starts, tt_base + body.end_byte),
                    start_byte=tt_base + start.start_byte,
                    end_byte=tt_base + body.end_byte,
                )
                for start, body in arms
            )
            brace = self._mapped_node(line_starts, tt_base, tt.children[0], node_type="{")
            close = self._mapped_node(line_starts, tt_base, tt.children[-1], node_type="}")
            container = ContainerView(
                type="match_block",
                named_children=units,
                children=(brace, *units, close),
            )

            container_diagnostics, _ = self._analyze_container(
                path=path,
                source=source,
                lines=lines,
                line_starts=line_starts,
                newline=newline,
                container=container,
                build_fixes=False,
            )

            diagnostics.extend(
                self._macro_violation(violation)
                for violation in container_diagnostics
            )

        # Arm bodies and any other direct brace groups are blocks or nested
        # match-like groups; recurse into them.
        for child in tt.children:
            if is_brace_token_tree(child):
                diagnostics.extend(
                    self._analyze_braced(
                        tt=child,
                        tt_base=tt_base,
                        path=path,
                        source=source,
                        lines=lines,
                        line_starts=line_starts,
                        newline=newline,
                    )
                )

        return diagnostics


def is_item_container(node: Node) -> bool:
    if node.type not in ITEM_CONTAINER_TYPES:
        return False

    if node.type == "source_file":
        return True

    return node.parent is not None and node.parent.type == "mod_item"


def iter_nodes(root: Node) -> list[Node]:
    """Collect all Node wrappers immediately.

    Do not turn this into a generator: the tree-sitter Python binding's
    Node wrappers can hit lifetime issues when iteration and GC interleave.
    """
    result: list[Node] = []
    stack = [root]

    while stack:
        node = stack.pop()
        result.append(node)

        # Read children eagerly to avoid accessing invalidated wrappers later.
        children = list(node.children)
        stack.extend(reversed(children))

    return result


def is_brace_token_tree(node: Node) -> bool:
    return node.type == "token_tree" and bool(node.children) and node.children[0].type == "{"


def brace_body(node: Node) -> Node | None:
    """The brace-delimited token_tree body of a macro invocation, if any."""
    return next((child for child in node.children if is_brace_token_tree(child)), None)


def match_like_arms(tt: Node) -> list[tuple[Node, Node]]:
    """Return `(pattern_start, body)` pairs for the top-level `=>` arms of a
    brace token_tree. Arms are split on `=>` (never on commas: a select!
    branch's `, if guard` comma lives inside the pattern)."""
    children = list(tt.children)
    length = len(children)
    arms: list[tuple[Node, Node]] = []
    index = 1  # skip the opening '{'

    while index < length:
        child = children[index]

        if child.type == "}":
            break

        if child.type == "," or child.type in COMMENT_NODE_TYPES | {"attribute_item"}:
            index += 1
            continue

        pattern_start = child
        arrow = index

        while arrow < length and children[arrow].type not in {"=>", "}"}:
            arrow += 1

        if arrow >= length or children[arrow].type == "}":
            break

        body = children[arrow + 1] if arrow + 1 < length else None

        if body is not None and is_brace_token_tree(body):
            arms.append((pattern_start, body))
            index = arrow + 2  # skip '=>' and its body
        else:
            index = arrow + 1

    return arms


def direct_units(container: Node) -> list[Node]:
    if container.type == "match_block":
        return [
            child for child in container.named_children if child.type == "match_arm"
        ]

    if container.type == "block":
        return [
            child
            for child in container.named_children
            if child.type not in COMMENT_NODE_TYPES | {"attribute_item"}
        ]

    if container.type == "field_declaration_list":
        return [
            child
            for child in container.named_children
            if child.type == "field_declaration"
        ]

    if container.type == "field_initializer_list":
        return [
            child
            for child in container.named_children
            if child.type == "field_initializer"
        ]

    if container.type == "enum_variant_list":
        return [
            child
            for child in container.named_children
            if child.type == "enum_variant"
        ]

    return []


def unit_anchor(container: Node, unit: Node) -> Node:
    """Return the first outer attribute belonging to a direct statement."""
    anchor = unit

    for child in reversed(container.named_children):
        if child.end_byte > anchor.start_byte:
            continue

        if child.type == "attribute_item":
            anchor = child
            continue

        if child.type in COMMENT_NODE_TYPES:
            continue

        break

    return anchor


ITEM_KIND_NAMES = {
    "use_declaration": "use declaration",
    "mod_item": "module",
    "struct_item": "struct",
    "enum_item": "enum",
    "union_item": "union",
    "impl_item": "impl block",
    "trait_item": "trait",
    "function_item": "function",
    "function_signature_item": "trait method",
    "const_item": "constant",
    "static_item": "static",
    "type_item": "type alias",
    "associated_type": "associated type",
    "macro_definition": "macro definition",
    "macro_invocation": "macro invocation",
    "expression_statement": "macro invocation",
    "extern_crate_declaration": "extern crate",
    "foreign_mod_item": "extern block",
}


def item_kind(node: Node) -> str:
    return ITEM_KIND_NAMES.get(node.type, node.type)


def item_anchor(container: Node, item: Node) -> Node:
    """Return the first leading outer attribute or comment belonging to an
    item, so a missing blank line is measured against the start of the whole
    attached group. `empty_statement` (the `;` after a top-level macro
    invocation) belongs to the previous item and never anchors."""
    anchor = item

    for child in reversed(container.named_children):
        if child.end_byte > anchor.start_byte:
            continue

        if child.type in ITEM_ATTACH_TYPES:
            anchor = child
            continue

        if child.type == "empty_statement":
            continue

        break

    return anchor


def unit_kind(container: Node) -> str:
    if container.type == "match_block":
        return "match arm"

    if container.type == "enum_variant_list":
        return "enum variant"

    if container.type == "field_declaration_list":
        return "struct field"

    return "statement"


def opening_brace(container: Node) -> Node | None:
    for child in container.children:
        if child.type == "{":
            return child

    return None


def line_is_only_open_brace(
    lines: list[str],
    brace: Node,
) -> bool:
    row = brace.start_point.row

    return 0 <= row < len(lines) and lines[row].strip() == "{"


def has_blank_line_between(
    lines: list[str],
    previous: Node,
    current: Node,
) -> bool:
    start_row = previous.end_point.row + 1
    end_row = min(current.start_point.row, len(lines))

    return any(lines[row].strip() == "" for row in range(start_row, end_row))


def separator_rows_before_first(
    *,
    lines: list[str],
    brace: Node,
    first: Node,
) -> list[int]:
    start_row = brace.start_point.row + 1
    end_row = min(first.start_point.row, len(lines))

    return [
        row
        for row in range(start_row, end_row)
        if BARE_SEPARATOR_RE.fullmatch(lines[row]) is not None
    ]


def comment_rows_before_first(
    *,
    lines: list[str],
    brace: Node,
    first: Node,
) -> list[int]:
    """Rows holding a real comment (not a bare `//` separator) between the
    opening brace and the first unit."""
    start_row = brace.start_point.row + 1
    end_row = min(first.start_point.row, len(lines))

    return [
        row
        for row in range(start_row, end_row)
        if (
            BARE_SEPARATOR_RE.fullmatch(lines[row]) is None
            and lines[row].lstrip().startswith("//")
        )
    ]


def direct_comments_between(
    container: Node,
    previous: Node,
    current: Node,
) -> list[Node]:
    return [
        child
        for child in container.named_children
        if (
            child.type in COMMENT_NODE_TYPES
            and child.start_byte >= previous.end_byte
            and child.end_byte <= current.start_byte
            and child.start_point.row > previous.end_point.row
        )
    ]


def build_block_start_separator_edit(
    *,
    source: bytes,
    lines: list[str],
    line_starts: list[int],
    newline: bytes,
    brace: Node,
    first: Node,
) -> TextEdit | None:
    del lines

    indent = indentation_bytes(
        source,
        line_starts,
        first,
    )

    # Common form:
    #
    # if condition {
    #     first_statement;
    # }
    #
    # Insert `//` on the line before the first statement.
    if first.start_point.row > brace.start_point.row:
        insertion_row = brace.start_point.row + 1

        if insertion_row >= len(line_starts):
            return None

        return TextEdit(
            start_byte=line_starts[insertion_row],
            end_byte=line_starts[insertion_row],
            replacement=indent + b"//" + newline,
            code="BLK000",
        )

    # Extreme inline form:
    #
    # if condition { first_statement; second_statement; }
    gap = source[brace.end_byte : first.start_byte]

    if gap.strip():
        return None

    return TextEdit(
        start_byte=brace.end_byte,
        end_byte=first.start_byte,
        replacement=(newline + indent + b"//" + newline + indent),
        code="BLK000",
    )


def build_item_blank_line_edit(
    *,
    line_starts: list[int],
    newline: bytes,
    anchor: Node,
) -> TextEdit | None:
    """Insert a blank line before the item's leading comment/attribute."""
    row = anchor.start_point.row

    if row >= len(line_starts):
        return None

    return TextEdit(
        start_byte=line_starts[row],
        end_byte=line_starts[row],
        replacement=newline,
        code="BLK003",
    )


def build_blank_line_edit(
    *,
    source: bytes,
    line_starts: list[int],
    newline: bytes,
    container: Node,
    previous: Node,
    current: Node,
) -> TextEdit | None:
    comments = direct_comments_between(
        container,
        previous,
        current,
    )

    # When an explanatory comment sits between two statements, insert the
    # blank line before the comment so it stays attached to the statement
    # that follows it.
    anchor = min(comments, key=lambda node: node.start_byte) if comments else current

    # Common multi-line form: insert a newline before the anchor line.
    if anchor.start_point.row > previous.end_point.row:
        row = anchor.start_point.row

        if row >= len(line_starts):
            return None

        return TextEdit(
            start_byte=line_starts[row],
            end_byte=line_starts[row],
            replacement=newline,
            code="BLK001",
        )

    # Multiple statements on one line:
    #
    # let a = 1; let b = 2;
    gap = source[previous.end_byte : anchor.start_byte]

    if gap.strip():
        return None

    indent = indentation_bytes(
        source,
        line_starts,
        anchor,
    )

    return TextEdit(
        start_byte=previous.end_byte,
        end_byte=anchor.start_byte,
        replacement=newline + newline + indent,
        code="BLK001",
    )


def build_redundant_separator_edits(
    *,
    lines: list[str],
    line_starts: list[int],
    brace: Node,
    first: Node,
    separator_rows: list[int],
) -> list[TextEdit]:
    region_rows = list(
        range(
            brace.start_point.row + 1,
            min(first.start_point.row, len(lines)),
        )
    )

    separator_set = set(separator_rows)

    remaining_rows = [row for row in region_rows if row not in separator_set]

    # If only blank lines and bare `//` sit between `{` and the first
    # statement, drop them all to restore a plain single-statement block.
    if all(lines[row].strip() == "" for row in remaining_rows):
        rows_to_remove = region_rows
    else:
        # Otherwise remove only the bare `//` lines.
        rows_to_remove = separator_rows

    edits: list[TextEdit] = []

    for start_row, end_row in contiguous_ranges(rows_to_remove):
        start_byte = line_starts[start_row]

        if end_row + 1 < len(line_starts):
            end_byte = line_starts[end_row + 1]
        else:
            end_byte = start_byte + len(lines[end_row].encode("utf-8"))

        edits.append(
            TextEdit(
                start_byte=start_byte,
                end_byte=end_byte,
                replacement=b"",
                code="BLK002",
            )
        )

    return edits


def contiguous_ranges(
    rows: list[int],
) -> list[tuple[int, int]]:
    if not rows:
        return []

    sorted_rows = sorted(set(rows))

    result: list[tuple[int, int]] = []
    start = sorted_rows[0]
    end = start

    for row in sorted_rows[1:]:
        if row == end + 1:
            end = row
            continue

        result.append((start, end))
        start = row
        end = row

    result.append((start, end))

    return result


def indentation_bytes(
    source: bytes,
    line_starts: list[int],
    node: Node,
) -> bytes:
    row = node.start_point.row

    if row >= len(line_starts):
        return b""

    line_start = line_starts[row]

    return source[line_start : node.start_byte]


def byte_line_starts(source: bytes) -> list[int]:
    starts = [0]

    for index, byte in enumerate(source):
        if byte == 0x0A:
            starts.append(index + 1)

    return starts


def detect_newline(source: bytes) -> bytes:
    first_lf = source.find(b"\n")

    if first_lf > 0 and source[first_lf - 1 : first_lf + 1] == b"\r\n":
        return b"\r\n"

    return b"\n"


def normalize_edits(
    edits: list[TextEdit],
) -> list[TextEdit]:
    unique = {
        (
            edit.start_byte,
            edit.end_byte,
            edit.replacement,
            edit.code,
        ): edit
        for edit in edits
    }

    result = sorted(
        unique.values(),
        key=lambda edit: (
            edit.start_byte,
            edit.end_byte,
            edit.code,
            edit.replacement,
        ),
    )

    previous: TextEdit | None = None

    for edit in result:
        if previous is not None and edit.start_byte < previous.end_byte:
            raise ValueError(
                "overlapping automatic spacing fixes were generated: "
                f"{previous} and {edit}"
            )

        previous = edit

    return result


def apply_edits(
    source: bytes,
    edits: tuple[TextEdit, ...],
) -> bytes:
    result = source

    # Apply in reverse byte-offset order so earlier insertions do not
    # shift the offsets of later edits.
    for edit in sorted(
        edits,
        key=lambda item: (
            item.start_byte,
            item.end_byte,
        ),
        reverse=True,
    ):
        result = result[: edit.start_byte] + edit.replacement + result[edit.end_byte :]

    return result


def iter_rs_files(
    paths: list[Path],
    ignore_dirs: set[str],
) -> list[Path]:
    files: list[Path] = []

    for path in paths:
        if path.is_file():
            if path.suffix == ".rs":
                files.append(path)

            continue

        if not path.is_dir():
            continue

        for child in path.rglob("*.rs"):
            if any(part in ignore_dirs for part in child.parts):
                continue

            files.append(child)

    return sorted(set(files))


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return spacing violations across every `.rs` file under root."""
    section = merged("spacing-style", config)
    ignore_dirs = set(section.get("ignore_dirs", []))
    root = root.resolve()
    checker = RustSpacingChecker(root)
    diagnostics: list[Violation] = []

    for path in iter_rs_files([root], ignore_dirs):
        try:
            analysis = checker.analyze_file(path)
            diagnostics.extend(analysis.diagnostics)
        except UnicodeDecodeError as error:
            print(
                f"{path}: failed to decode as UTF-8: {error}",
                file=sys.stderr,
            )

            continue
        except OSError as error:
            print(
                f"{path}: failed to read file: {error}",
                file=sys.stderr,
            )

            continue

    return sorted(
        diagnostics,
        key=lambda diagnostic: (
            str(diagnostic.path),
            diagnostic.line,
            diagnostic.column,
            diagnostic.code,
        ),
    )


def fix(root: Path, config: dict | None = None) -> list[Violation]:
    """Apply BLK000, BLK001, and BLK002 fixes, then return remaining violations."""
    section = merged("spacing-style", config)
    ignore_dirs = set(section.get("ignore_dirs", []))
    root = root.resolve()
    checker = RustSpacingChecker(root)
    files = iter_rs_files([root], ignore_dirs)
    changed_files = 0
    applied_edits = 0
    skipped_parse_error_files = 0

    for path in files:
        try:
            source = path.read_bytes()
            analysis_source = production_source(path, checker.root)

            analysis = checker.analyze_source(
                path,
                analysis_source,
                build_fixes=True,
            )
        except UnicodeDecodeError as error:
            print(
                f"{path}: failed to decode as UTF-8: {error}",
                file=sys.stderr,
            )

            continue
        except OSError as error:
            print(
                f"{path}: failed to read file: {error}",
                file=sys.stderr,
            )

            continue
        except ValueError as error:
            print(
                f"{path}: failed to build fixes: {error}",
                file=sys.stderr,
            )

            continue

        # Files with Rust parse errors are not modified, since incomplete
        # node ranges could produce wrong fixes.
        if analysis.has_parse_errors:
            skipped_parse_error_files += 1
            continue

        if not analysis.edits:
            continue

        fixed_source = apply_edits(
            source,
            analysis.edits,
        )

        if fixed_source == source:
            continue

        path.write_bytes(fixed_source)

        changed_files += 1
        applied_edits += len(analysis.edits)

    remaining = check(root, config)

    print(
        f"fixed {applied_edits} spacing issue(s) "
        f"in {changed_files} file(s); "
        f"{len(remaining)} diagnostic(s) remain"
    )

    if skipped_parse_error_files:
        print(
            (
                f"skipped {skipped_parse_error_files} file(s) "
                "containing Rust parse errors"
            ),
            file=sys.stderr,
        )

    return remaining


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="spacing-style",
        description=(
            "Check and automatically fix custom Rust spacing rules "
            "between direct block statements and match arms."
        ),
    )

    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(".")],
        help=("Rust files or directories. Defaults to the current directory."),
    )

    parser.add_argument(
        "--fix",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Apply BLK000, BLK001, and BLK002 fixes in place, "
            "then run the checker again. Files containing Rust "
            "parse errors are not changed. Pass --no-fix to check only."
        ),
    )

    args = parser.parse_args()

    section = merged("spacing-style", None)
    ignore_dirs = set(section.get("ignore_dirs", []))
    checker = RustSpacingChecker(Path.cwd().resolve())
    files = iter_rs_files(args.paths, ignore_dirs)

    if not args.fix:
        diagnostics: list[Violation] = []

        for path in files:
            try:
                analysis = checker.analyze_file(path)
                diagnostics.extend(analysis.diagnostics)
            except UnicodeDecodeError as error:
                print(
                    f"{path}: failed to decode as UTF-8: {error}",
                    file=sys.stderr,
                )

                return 2
            except OSError as error:
                print(
                    f"{path}: failed to read file: {error}",
                    file=sys.stderr,
                )

                return 2

        for diagnostic in sorted(
            diagnostics,
            key=lambda item: (str(item.path), item.line, item.column, item.code),
        ):
            print(
                f"{diagnostic.path}:{diagnostic.line}:{diagnostic.column}: "
                f"{diagnostic.code}: {diagnostic.message}"
            )

        return 1 if diagnostics else 0

    remaining = fix(checker.root)

    for diagnostic in remaining:
        print(
            f"{diagnostic.path}:{diagnostic.line}:{diagnostic.column}: "
            f"{diagnostic.code}: {diagnostic.message}"
        )

    return 1 if remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())
