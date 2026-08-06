"""Enforce custom Rust block-spacing rules: block-start separators, blank
lines between direct statements, match arms, and enum variants."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
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
