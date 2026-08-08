"""Prefer `if let`/`let ... else` over matches with a single business arm.

A match should express several business branches. When exactly one arm does
real work and every other arm only bails out (`return`, `break`,
`continue`, a diverging macro, or an empty body), the match is a single-path
dispatch and reads better as a guard:

    match value {
        Some(x) => foo(x),
        None => return,
    }

becomes:

    let Some(x) = value else {
        return;
    };

    foo(x);

An all-empty guard set becomes `if let` instead:

    match value {
        Some(x) => foo(x),
        None => {}
    }

becomes:

    if let Some(x) = value {
        foo(x);
    }

The conversion is skipped whenever collapsing the guards into one `else`
block (or a missing `else`) would change behavior: multi-arm dispatch,
wildcard fallbacks, match guards, or guards that diverge in different ways.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation
from ..config import merged
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))

# Expression node types that unconditionally diverge control flow.
DIVERGING_EXPR_TYPES = {
    "return_expression",
    "break_expression",
    "continue_expression",
}

# Arm bodies that mean "do nothing".
EMPTY_VALUE_TYPES = {"unit_expression"}


def node_text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def excluded_path(path: Path, config: dict) -> bool:
    exclude_segments = config.get("exclude_segments", [])
    exclude_prefixes = config.get("exclude_filename_prefixes", [])
    exclude_filenames = config.get("exclude_filenames", [])

    if any(part in exclude_segments for part in path.parts):
        return True

    if any(part.startswith(prefix) for part in path.parts for prefix in exclude_prefixes):
        return True

    return path.name in exclude_filenames


def rust_files(root: Path, config: dict) -> list[Path]:
    return sorted(
        path
        for path in (root / "src").rglob("*.rs")
        if not excluded_path(path.relative_to(root), config)
    )


def macro_name(source: bytes, node: tree_sitter.Node) -> str | None:
    macro = node.child_by_field_name("macro")

    if macro is None:
        return None

    return node_text(source, macro).split("::")[-1]


def is_diverging_expr(node: tree_sitter.Node, source: bytes, macros: frozenset[str]) -> bool:
    if node.type in DIVERGING_EXPR_TYPES:
        return True

    if node.type == "macro_invocation":
        return macro_name(source, node) in macros

    return False


def is_diverging_block(block: tree_sitter.Node, source: bytes, macros: frozenset[str]) -> bool:
    """True when the block's tail expression always diverges.

    Only the trailing expression decides: a leading `if`, `for`, `loop`, or
    `match` may or may not execute, so a block that merely *contains* a
    return further up is not a pure guard.
    """

    if not block.named_children:
        return False

    last = block.named_children[-1]

    if last.type == "expression_statement":
        named = [child for child in last.named_children]

        if not named:
            return False

        return is_diverging_expr(named[0], source, macros)

    return is_diverging_expr(last, source, macros)


def classify_arm(arm: tree_sitter.Node, source: bytes, macros: frozenset[str]) -> str:
    """Return "guarded", "empty", "diverging", or "business"."""

    pattern = arm.child_by_field_name("pattern")

    if pattern is None:
        return "guarded"

    if pattern.child_by_field_name("condition") is not None:
        return "guarded"

    value = arm.child_by_field_name("value")

    if value is None:
        return "guarded"

    if value.type == "block" and not value.named_children:
        return "empty"

    if value.type in EMPTY_VALUE_TYPES:
        return "empty"

    if is_diverging_block(value, source, macros) if value.type == "block" else is_diverging_expr(value, source, macros):
        return "diverging"

    return "business"


def match_scrutinee(match: tree_sitter.Node, source: bytes) -> str:
    for child in match.named_children:
        if child.type == "match_block":
            break

        return node_text(source, child)

    return "?"


def convertible(
    arms: list[tree_sitter.Node],
    kinds: list[str],
    source: bytes,
) -> str | None:
    """Return "if-let" or "let-else" when the match converts cleanly."""

    if len(arms) < 2 or "guarded" in kinds:
        return None

    business = [i for i, kind in enumerate(kinds) if kind == "business"]
    guards = [i for i, kind in enumerate(kinds) if kind != "business"]

    if len(business) != 1 or not guards:
        return None

    pattern = arms[business[0]].child_by_field_name("pattern")

    if pattern is not None and node_text(source, pattern).strip() == "_":
        return None

    guard_kinds = {kinds[i] for i in guards}

    if guard_kinds == {"empty"}:
        return "if-let"

    if guard_kinds == {"diverging"}:
        bodies = {node_text(source, arms[i].child_by_field_name("value")) for i in guards}

        if len(bodies) == 1:
            return "let-else"

    return None


def violation_for(
    match: tree_sitter.Node,
    root: Path,
    path: Path,
    kind: str,
    scrutinee: str,
    pattern: str,
) -> Violation:
    if kind == "let-else":
        message = (
            f"match has a single business arm `{pattern}` and a diverging guard; "
            f"prefer `let {pattern} = {scrutinee} else {{ ... }}` over a match "
            "whose only other arms bail out"
        )
    else:
        message = (
            f"match has a single business arm `{pattern}` and only empty guard "
            f"arms; prefer `if let {pattern} = {scrutinee} {{ ... }}`"
        )

    return Violation(
        path=path.relative_to(root),
        line=match.start_point.row + 1,
        column=match.start_point.column + 1,
        code="LET001",
        message=message,
    )


def check_file(path: Path, root: Path, macros: frozenset[str]) -> list[Violation]:
    source = production_source(path, root)
    tree = PARSER.parse(source)
    violations: list[Violation] = []
    pending = [tree.root_node]

    while pending:
        node = pending.pop()

        if node.type == "match_expression":
            body = node.child_by_field_name("body")
            arms = (
                [child for child in body.named_children if child.type == "match_arm"]
                if body is not None
                else []
            )

            kinds = [classify_arm(arm, source, macros) for arm in arms]
            kind = convertible(arms, kinds, source)

            if kind is not None:
                business = next(
                    index for index, arm_kind in enumerate(kinds) if arm_kind == "business"
                )
                pattern = arms[business].child_by_field_name("pattern")
                violations.append(
                    violation_for(
                        node,
                        root,
                        path,
                        kind,
                        match_scrutinee(node, source),
                        node_text(source, pattern),
                    )
                )

        pending.extend(reversed(node.named_children))

    return violations


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return single-business match violations under src/."""
    root = root.resolve()
    section = merged("prefer-if-let-guard", config)
    macros = frozenset(section.get("diverging_macros", ["panic", "unreachable", "todo", "unimplemented"]))

    return [
        violation
        for path in rust_files(root, section)
        for violation in check_file(path, root, macros)
    ]


def self_test() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        fixture = src / "fixture.rs"

        # ── flagged: diverging guard ─────────────────────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => return,\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: diverging guard not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        if "let Some(x) = value else" not in violations[0].message:
            print(f"self-test: wrong suggestion for diverging guard: {violations[0].message}", file=sys.stderr)
            return 1

        # ── flagged: empty guard ─────────────────────────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => {},\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: empty guard not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        if "if let Some(x) = value" not in violations[0].message:
            print(f"self-test: wrong suggestion for empty guard: {violations[0].message}", file=sys.stderr)
            return 1

        # ── flagged: unit-expression guard counts as empty ───────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        _ => (),\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: unit guard not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        # ── flagged: diverging block with leading statements ─────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => {\n"
            "            warn(\"missing\");\n"
            "            return;\n"
            "        },\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: diverging block not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        # ── flagged: diverging macro guard ───────────────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => unreachable!(),\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: macro guard not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        # ── flagged: identical multi-guard collapses into one else ──

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => return,\n"
            "        _ => return,\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: identical multi-guard not flagged; got {len(violations)}", file=sys.stderr)
            return 1

        # ── flagged: `//` separator comments are not match arms ─────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    let ok = match value {\n"
            "        //\n"
            "        // Internal implementation detail.\n"
            "        Some(x) => x,\n"
            "\n"
            "        None => return,\n"
            "    };\n"
            "}\n",
        )
        violations = check(root)

        if len(violations) != 1:
            print(f"self-test: comment separators broke detection; got {len(violations)}", file=sys.stderr)
            return 1

        # ── kept: two business arms ──────────────────────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => bar(),\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if violations:
            print(f"self-test: two-business match was flagged", file=sys.stderr)
            return 1

        # ── kept: multi-pattern dispatch ─────────────────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => a(x),\n"
            "        Some(y) => b(y),\n"
            "        None => return,\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if violations:
            print(f"self-test: multi-pattern match was flagged", file=sys.stderr)
            return 1

        # ── kept: wildcard business fallback ─────────────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        _ => default(),\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if violations:
            print(f"self-test: wildcard fallback was flagged", file=sys.stderr)
            return 1

        # ── kept: match guard cannot be expressed with if-let ────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) if x > 5 => foo(x),\n"
            "        None => return,\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if violations:
            print(f"self-test: match guard was flagged", file=sys.stderr)
            return 1

        # ── kept: mixed empty and diverging guards ───────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => {},\n"
            "        _ => return,\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if violations:
            print(f"self-test: mixed guards were flagged", file=sys.stderr)
            return 1

        # ── kept: guards diverging in different ways ─────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => return,\n"
            "        _ => panic!(\"impossible\"),\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if violations:
            print(f"self-test: distinct diverging guards were flagged", file=sys.stderr)
            return 1

        # ── kept: guard block whose tail does not diverge ────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "        None => {\n"
            "            if x {\n"
            "                return;\n"
            "            }\n"
            "        },\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if violations:
            print(f"self-test: conditional guard block was flagged", file=sys.stderr)
            return 1

        # ── kept: single-arm match ───────────────────────────────────

        fixture.write_text(
            "pub fn f(value: Option<u8>) {\n"
            "    match value {\n"
            "        Some(x) => foo(x),\n"
            "    }\n"
            "}\n",
        )
        violations = check(root)

        if violations:
            print(f"self-test: single-arm match was flagged", file=sys.stderr)
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="prefer-if-let-guard")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    violations = check(args.root.resolve())

    for violation in violations:
        location = (
            f"{violation.path}:{violation.line}:{violation.column}"
            if violation.column is not None
            else f"{violation.path}:{violation.line}"
        )
        print(
            f"{location}: {violation.code}: {violation.message}",
            file=sys.stderr,
        )

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
