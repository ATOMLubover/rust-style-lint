"""Check that user-defined identifiers carry the required comment style.

Scans every Rust source file under src/ and reports declarations that are
not immediately preceded by the required comment:

- public items require an outer doc comment (`///` or `/**`);
- private items require a regular comment (`//` or `/*`).

Covered declaration kinds
-------------------------
- Module-level items:  module declaration, function, struct, enum, trait,
  type alias, const, static, macro definition, union.
- Trait members:       associated functions, type aliases, and constants
  inside a trait definition (implicitly public).
- Enum variants:       each variant of an enum (implicitly public).
- Inherent methods:    public functions inside an impl block.

Skipped items
-------------
- Items annotated with `#[test]`, `#[tokio::test]`, or `#[rstest::...]`.
- The `main` function in `src/main.rs`.
- Inner doc comments (`//!`, `/*!`) — these document the enclosing module,
  not the following item.

Config
------
``exclude_segments``, ``exclude_filename_prefixes``, and
``exclude_filenames`` control which paths are treated as test fixtures or
generated code and skipped entirely.
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

ITEM_KINDS = (
    "associated_type",
    "const_item",
    "enum_item",
    "enum_variant",
    "field_declaration",
    "function_item",
    "function_signature_item",
    "macro_definition",
    "mod_item",
    "static_item",
    "struct_item",
    "trait_item",
    "type_item",
    "union_item",
)

def excluded_path(path: Path, config: dict) -> bool:
    """Return True for test fixtures and generated code."""

    exclude_segments = config.get("exclude_segments", [])
    exclude_prefixes = config.get("exclude_filename_prefixes", [])
    exclude_filenames = config.get("exclude_filenames", [])

    parts = path.parts

    if any(part in exclude_segments for part in parts):
        return True

    if any(part.startswith(prefix) for part in parts for prefix in exclude_prefixes):
        return True

    return path.name in exclude_filenames


def rust_files(root: Path, config: dict) -> list[Path]:
    return sorted(
        path
        for path in (root / "src").rglob("*.rs")
        if not excluded_path(path.relative_to(root), config)
    )


def descendants(node: tree_sitter.Node, kinds: tuple[str, ...]) -> list[tree_sitter.Node]:
    found: list[tree_sitter.Node] = []
    pending = [node]

    while pending:
        current = pending.pop()

        if current.type in kinds:
            found.append(current)

        pending.extend(reversed(current.named_children))

    return found


def text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def is_public(declaration: tree_sitter.Node, source: bytes) -> bool:
    """Return True when the item is visible outside its defining module."""

    for child in declaration.children:
        if child.type == "visibility_modifier":
            return True

    if declaration.type == "enum_variant":
        return True

    if declaration.type == "field_declaration":
        current = declaration.parent

        while current is not None:
            if current.type == "struct_item":

                for child in current.children:
                    if child.type == "visibility_modifier":
                        return True

            if current.type in ("enum_item", "union_item"):

                for child in current.children:
                    if child.type == "visibility_modifier":
                        return True

            current = current.parent

        return False

    current = declaration.parent

    while current is not None:
        if current.type == "trait_item":
            return True

        if current.type in ("closure_expression", "function_item"):
            return False

        current = current.parent

    return False


def is_test_item(declaration: tree_sitter.Node, source: bytes) -> bool:
    """Return True when the item carries a test-related attribute."""

    parent = declaration.parent

    if parent is None:
        return False

    idx = _sibling_index(parent, declaration)

    if idx is None:
        return False

    for i in range(idx - 1, -1, -1):
        sibling = parent.child(i)

        if sibling.type in ("attribute_item", "line_comment", "block_comment"):
            if sibling.type == "attribute_item":
                attr = text(source, sibling)

                if "#[test" in attr or "#[tokio::test" in attr or "rstest" in attr:
                    return True

            continue

        break

    return False


def _sibling_index(parent: tree_sitter.Node, child: tree_sitter.Node) -> int | None:
    """Return the index of *child* in *parent*'s children, or None."""

    for i in range(parent.child_count):
        sibling = parent.child(i)

        if (
            sibling.start_byte == child.start_byte
            and sibling.end_byte == child.end_byte
        ):
            return i

    return None


def is_main_in_main_rs(declaration: tree_sitter.Node, source: bytes, path: Path) -> bool:
    """Return True when the declaration is the `main` function in main.rs."""

    if declaration.type != "function_item":
        return False

    if path.name != "main.rs":
        return False

    name = declaration.child_by_field_name("name")

    if name is None:
        return False

    return text(source, name) == "main"


def has_comment(
    declaration: tree_sitter.Node,
    source: bytes,
    *,
    is_doc_comment: bool,
) -> bool:
    """Return True when the declaration has the requested preceding comment.

    Attributes may appear between a comment and its declaration. Private
    declarations deliberately reject doc comments so their implementation
    notes do not become part of the generated public documentation.
    """

    parent = declaration.parent

    if parent is None:
        return False

    idx = _sibling_index(parent, declaration)

    if idx is None:
        return False

    for i in range(idx - 1, -1, -1):
        sibling = parent.child(i)

        if sibling.type == "attribute_item":
            continue

        if sibling.type == "line_comment":
            prefix = source[sibling.start_byte : sibling.start_byte + 3]

            if is_doc_comment:
                return prefix == b"///"

            # A bare `//` (empty content) is a block separator, not a
            # comment, and does not satisfy the coverage rule.
            content = source[sibling.start_byte + 2 : sibling.end_byte].strip()

            return prefix[:2] == b"//" and prefix != b"///" and bool(content)

        if sibling.type == "block_comment":
            prefix = source[sibling.start_byte : sibling.start_byte + 3]

            if is_doc_comment:
                return prefix == b"/**"

            content = source[sibling.start_byte + 2 : sibling.end_byte - 2].strip()

            return (
                prefix[:2] == b"/*"
                and prefix != b"/**"
                and prefix != b"/*!"
                and bool(content)
            )

        return False

    return False


def check_file(path: Path, root: Path) -> list[Violation]:
    source = production_source(path, root)
    tree = PARSER.parse(source)
    violations: list[Violation] = []

    for declaration in descendants(tree.root_node, ITEM_KINDS):
        if is_test_item(declaration, source):
            continue

        if is_main_in_main_rs(declaration, source, path):
            continue

        public = is_public(declaration, source)

        if has_comment(declaration, source, is_doc_comment=public):
            continue

        name_node = declaration.child_by_field_name("name")

        if name_node is None:
            continue

        name = text(source, name_node)

        visibility = "public" if public else "private"
        comment_kind = "doc comment" if public else "regular comment"
        violations.append(
            Violation(
                path=path.relative_to(root),
                line=name_node.start_point.row + 1,
                code="DOC001",
                message=(
                    f"{visibility} {declaration.type.replace('_', ' ')} "
                    f"'{name}' is missing a {comment_kind}"
                ),
            ),
        )

    return violations


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return comment-coverage violations under src/."""
    section = merged("doc-comment-coverage", config)

    return [
        violation
        for path in rust_files(root, section)
        for violation in check_file(path, root)
    ]


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def self_test() -> int:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        fixture = src / "fixture.rs"

        # ── valid: documented items ───────────────────────────────────

        fixture.write_text(
            "/// A documented public function.\n"
            "pub fn documented_fn() {}\n"
            "\n"
            "/** A documented public struct. */\n"
            "pub struct DocumentedStruct;\n"
            "\n"
            "/// A documented trait.\n"
            "pub trait DocumentedTrait {\n"
            "    /// A documented trait method.\n"
            "    fn trait_method(&self);\n"
            "}\n"
            "\n"
            "/// A documented enum.\n"
            "pub enum DocumentedEnum {\n"
            "    /// A documented variant.\n"
            "    First,\n"
            "}\n"
            "\n"
            "/// A documented type alias.\n"
            "pub type DocumentedType = u32;\n"
            "\n"
            "/// A documented const.\n"
            "pub const ANSWER: u32 = 42;\n"
            "\n"
            "/// A documented module.\n"
            "pub mod documented_mod;\n"
        )

        if check(root):
            print("self-test: valid documented fixture was rejected", file=sys.stderr)
            return 1

        # ── invalid: undocumented items ──────────────────────────────

        fixture.write_text(
            "pub fn undocumented_fn() {}\n"
            "\n"
            "pub struct UndocumentedStruct;\n"
            "\n"
            "pub trait UndocumentedTrait {\n"
            "    fn undocumented_trait_method(&self);\n"
            "}\n"
            "\n"
            "pub enum UndocumentedEnum {\n"
            "    FirstVariant,\n"
            "}\n"
            "\n"
            "pub type UndocumentedType = u32;\n"
            "\n"
            "/// This one is documented.\n"
            "pub fn documented_fn() {}\n"
            "\n"
            "// This is a regular comment, NOT a doc comment.\n"
            "pub fn still_undocumented() {}\n"
            "\n"
            "pub mod undocumented_mod;\n"
        )

        violations = check(root)

        # Expected violations:
        #   1. undocumented_fn          (function_item)
        #   2. UndocumentedStruct       (struct_item)
        #   3. UndocumentedTrait        (trait_item)
        #   4. undocumented_trait_method (function_item inside trait)
        #   5. UndocumentedEnum         (enum_item)
        #   6. FirstVariant             (enum_variant)
        #   7. UndocumentedType         (type_item)
        #   8. still_undocumented       (function_item with // comment)
        #   9. undocumented_mod         (mod_item)
        expected = 9

        if len(violations) != expected:
            print(
                f"self-test: expected {expected} violations, got {len(violations)}",
                file=sys.stderr,
            )
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

        # ── test item annotated with #[test] is skipped ──────────────

        fixture.write_text(
            "#[test]\n"
            "pub fn this_is_a_test() {}\n"
            "\n"
            "#[tokio::test]\n"
            "pub async fn async_test() {}\n"
        )

        if check(root):
            print("self-test: test functions were not skipped", file=sys.stderr)
            return 1

        # ── private items require regular comments ───────────────────

        fixture.write_text(
            "// A documented implementation detail.\n"
            "fn private_fn() {}\n"
            "\n"
            "/* A private implementation type. */\n"
            "struct PrivateStruct;\n"
            "\n"
            "// A private module.\n"
            "mod private_mod;\n"
        )

        if check(root):
            print("self-test: commented private items were rejected", file=sys.stderr)
            return 1

        fixture.write_text(
            "fn uncommented_private_fn() {}\n"
            "\n"
            "/// A doc comment is not a private implementation comment.\n"
            "struct WronglyDocumentedPrivateStruct;\n"
        )

        violations = check(root)

        if len(violations) != 2:
            print(
                f"self-test: expected 2 private-comment violations, got {len(violations)}",
                file=sys.stderr,
            )
            print("\n".join(str(violation) for violation in violations), file=sys.stderr)
            return 1

        # ── attributes between doc comment and item are allowed ──────

        fixture.write_text(
            "/// Documented with an attribute in between.\n"
            "#[derive(Debug)]\n"
            "pub struct Attributed;\n"
        )

        if check(root):
            print("self-test: attribute-interleaved doc comment was rejected", file=sys.stderr)
            return 1

    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(prog="doc-comment-coverage")
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
