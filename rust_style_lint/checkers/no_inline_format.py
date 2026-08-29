"""Forbid inline named captures in Rust format strings — all args positional.

`format!("hello, {name}")` must be written `format!("hello, {}", name)`.
`{}`, `{0}`, and format-spec-only braces (`{:?}`) stay valid; `{{`/`}}`
escapes are literal braces and never captures.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation, source_files
from ..config import merged


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))

_CAPTURE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def rust_files(root: Path) -> list[Path]:
    return source_files(root)


def node_text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def string_content(literal: tree_sitter.Node, source: bytes) -> str:
    for child in literal.named_children:
        if child.type == "string_content":
            return node_text(source, child)

    return node_text(source, literal)[1:-1]


def named_captures(format_string: str) -> list[str]:
    """Return inline capture names inside a Rust format string.

    `{{`/`}}` escapes are literal braces, not captures. `{}`, `{0}`, and
    format-spec-only braces (`{:?}`, `{:<10}`) are positional and never flag.
    """
    names: list[str] = []
    index = 0
    length = len(format_string)

    while index < length:
        char = format_string[index]

        if char == "{":
            if index + 1 < length and format_string[index + 1] == "{":
                index += 2
                continue

            end = format_string.find("}", index + 1)

            if end == -1:
                break

            content = format_string[index + 1 : end]

            if content and not content.startswith(":"):
                name = content.split(":", 1)[0]

                if not name.isdigit() and _CAPTURE_NAME.fullmatch(name) is not None:
                    names.append(name)

            index = end + 1
        elif char == "}":
            index += 2 if (index + 1 < length and format_string[index + 1] == "}") else 1
        else:
            index += 1

    return names


def check_macro(
    node: tree_sitter.Node,
    source: bytes,
    path: Path,
    root: Path,
    macro_slots: dict[str, int],
) -> list[Violation]:
    name_node = node.child_by_field_name("macro")

    if name_node is None:
        return []

    macro_name = node_text(source, name_node)

    if macro_name not in macro_slots:
        return []

    token_tree = next((child for child in node.children if child.type == "token_tree"), None)

    if token_tree is None:
        return []

    # The configured slot identifies the format-string argument. Only a literal
    # at that exact slot is checked; a variable or nested macro is skipped, and
    # later string literals are data arguments rather than format strings.
    arguments = token_tree.named_children
    slot = macro_slots[macro_name]

    if slot >= len(arguments) or arguments[slot].type not in {"string_literal", "raw_string_literal"}:
        return []

    literal = arguments[slot]
    captures = named_captures(string_content(literal, source))

    if not captures:
        return []

    line = literal.start_point.row + 1
    column = literal.start_point.column + 1

    return [
        Violation(
            path=path.relative_to(root),
            line=line,
            column=column,
            code="FMT001",
            message=(
                f"format string uses inline capture '{{{name}}}'; "
                f"pass '{name}' positionally to {macro_name}!"
            ),
        )
        for name in dict.fromkeys(captures)
    ]


def check_file(path: Path, root: Path, macro_slots: dict[str, int]) -> list[Violation]:
    source = path.read_bytes()
    tree = PARSER.parse(source)
    violations: list[Violation] = []
    pending = [tree.root_node]

    while pending:
        node = pending.pop()

        if node.type == "macro_invocation":
            violations.extend(check_macro(node, source, path, root, macro_slots))

        pending.extend(reversed(node.named_children))

    return violations


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return violations for every inline capture in format strings under src/."""
    section = merged("no-inline-format", config)
    macro_slots = {
        **{str(name): 0 for name in section.get("macros", [])},
        **{str(name): 1 for name in section.get("writer_macros", [])},
    }

    return [
        violation
        for path in rust_files(root)
        for violation in check_file(path, root, macro_slots)
    ]


def self_test() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source_dir = root / "src"
        source_dir.mkdir()

        (source_dir / "lib.rs").write_text(
            "pub fn main() {\n"
            "    let name = \"x\";\n"
            "    let value = 1u8;\n"
            "    let errno = 5;\n"
            "    let f = &mut String::new();\n"
            "    format!(\"hello, {name}\");\n"
            "    println!(\"x={x}, y={y}\", x = 1, y = 2);\n"
            "    write!(f, \"{value:?}\", value);\n"
            "    panic!(\"err: {errno}\", errno);\n"
            "    format!(r#\"raw {name}\"#, name);\n"
            "}\n",
        )

        violations = check(root)
        codes = sorted({violation.code for violation in violations})

        if codes != ["FMT001"] or len(violations) != 6:
            print(
                f"self-test: inline captures were not all flagged; got {len(violations)} violations",
                file=sys.stderr,
            )

            for violation in violations:
                print(f"  {violation}", file=sys.stderr)

            return 1

        (source_dir / "lib.rs").write_text(
            "pub fn main() {\n"
            "    let name = \"x\";\n"
            "    format!(\"hello, {}\", name);\n"
            "    println!(\"{0}/{1}\", 1, 2);\n"
            "    println!(\"spec: {:?} {:.2}\", name, 3.14);\n"
            "    println!(\"literal braces: {{name}}\");\n"
            "    let msg = \"{name}\";\n"
            "    format!(msg, name);\n"
            "    format!(\"{}\", msg);\n"
            "    format!(\"{}\", \"{name}\");\n"
            "}\n",
        )

        violations = check(root)

        if violations:
            print("self-test: valid positional/escaped/data strings were flagged", file=sys.stderr)
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

        (source_dir / "lib.rs").write_text(
            "fn fmt(f: &mut String) {\n"
            "    let x = 1;\n"
            "    write!(f, \"x={x}\", x);\n"
            "    writeln!(f, \"value {value:?}\", value = x);\n"
            "}\n",
        )

        violations = check(root)

        if len(violations) != 2:
            print(
                f"self-test: write!/writeln! second-argument captures not flagged; got {len(violations)}",
                file=sys.stderr,
            )
            return 1

        (source_dir / "lib.rs").write_text(
            "fn f(g: impl Fn() -> u8) {\n"
            "    let w = 0u8;\n"
            "    write!(g(), \"{}\", w);\n"
            "    writeln!(w, \"done\");\n"
            "}\n",
        )

        violations = check(root)

        if violations:
            print("self-test: write! without literal format string was flagged", file=sys.stderr)
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="no-inline-format")
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
