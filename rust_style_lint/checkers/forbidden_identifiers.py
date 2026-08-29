"""Detect forbidden words in Rust identifier names.

Scans every Rust source file under src/ and reports identifiers whose name
segments contain words listed in the forbidden-word table. Also flags the
``target_`` prefix.

Rules
-----

* ``error`` is always forbidden (FBD003).
* ``err`` is the replacement for ``error``, but its form is restricted (FBD004):

  - Function names: only ``_err`` **suffix** is allowed (e.g. ``parse_err``).
  - Local variables / parameters: only ``err_`` **prefix** is allowed, AND the
    binding must NOT be an Error type.
  - Every other ``err`` form (bare ``err``, middle-segment, const, static,
    field, enum variant) is forbidden.

* Identifiers inside ``#[cfg(test)]`` modules are skipped entirely.

Config
------
``words``, ``prefixes``, and ``contextual_word`` define all identifier rules.
``allowed_modules`` exempts configured Rust modules and their descendants.
``skip_module_paths`` exempts files whose relative path contains any entry.
``ignore_files`` and ``exclude_filenames`` skip individual source files.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import tree_sitter
import tree_sitter_rust

from ..base import Violation, source_files, source_root
from ..config import merged
from ..production_source import production_source


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))

# ---------------------------------------------------------------------------
# declaration kinds whose `name` field is a definition site
# ---------------------------------------------------------------------------

DECLARATION_KINDS: tuple[str, ...] = (
    "const_item",
    "enum_item",
    "enum_variant",
    "field_declaration",
    "function_item",
    "function_signature_item",
    "static_item",
    "struct_item",
    "trait_item",
    "type_item",
    "union_item",
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def rust_files(root: Path, config: dict) -> list[Path]:
    exclude_filenames = set(config.get("exclude_filenames", []))

    return sorted(
        path
        for path in source_files(root)
        if path.name not in exclude_filenames
    )


def resolve_ignore_files(
    root: Path,
    ignore_files: list[Path],
    ignore_lists: list[Path],
) -> set[Path]:
    """Resolve direct ignore paths and newline-delimited ignore lists."""
    configured_files = list(ignore_files)

    for ignore_list in ignore_lists:
        ignore_list_path = (
            ignore_list
            if ignore_list.is_absolute()
            else root / ignore_list
        )

        for line in ignore_list_path.read_text().splitlines():
            entry = line.strip()

            if entry and not entry.startswith("#"):
                configured_files.append(Path(entry))

    return {
        (path if path.is_absolute() else root / path).resolve()
        for path in configured_files
    }


def split_identifier(name: str) -> list[str]:
    """Split snake_case, SCREAMING_SNAKE_CASE, PascalCase, or camelCase into
    lowercase word segments."""

    if "_" in name:
        return name.lower().split("_")

    # PascalCase / camelCase:
    #   IOError   -> IO_Error   -> io, error
    #   ParseErr  -> Parse_Err  -> parse, err
    #   XMLParser -> XML_Parser -> xml, parser
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)

    return s.lower().split("_")


def segments_contain_word(segments: list[str], word: str) -> bool:
    return word in segments


def error_line(
    path: Path,
    root: Path,
    node: tree_sitter.Node,
    code: str,
    message: str,
) -> Violation:
    return Violation(
        path=path.relative_to(root),
        line=node.start_point.row + 1,
        code=code,
        message=message,
    )


def file_module(path: Path, root: Path) -> tuple[str, ...]:
    relative = path.relative_to(source_root(path, root))
    parts = list(relative.with_suffix("").parts)

    if parts[-1] in {"lib", "main", "mod"}:
        parts.pop()

    return tuple(parts)


def format_module(module: tuple[str, ...]) -> str:
    return "crate" if not module else "crate::" + "::".join(module)


def module_is_allowed(module: str, allowed_modules: frozenset[str]) -> bool:
    return any(
        module == allowed or module.startswith(allowed + "::")
        for allowed in allowed_modules
    )


# ---------------------------------------------------------------------------
# cfg(test) detection
# ---------------------------------------------------------------------------

def _leading_attributes(
    node: tree_sitter.Node,
    source: bytes,
) -> list[tree_sitter.Node]:
    parent = node.parent

    if parent is None:
        return []

    index = next(
        (
            i
            for i, sib in enumerate(parent.children)
            if sib.start_byte == node.start_byte and sib.end_byte == node.end_byte
        ),
        None,
    )

    if index is None:
        return []

    attrs: list[tree_sitter.Node] = []

    for sib in reversed(parent.children[:index]):
        if sib.type == "attribute_item":
            attrs.append(sib)
            continue

        if not sib.is_named:
            continue

        break

    attrs.reverse()

    return attrs


def _is_cfg_test_attr(attr: tree_sitter.Node, source: bytes) -> bool:
    raw = text(source, attr)

    return re.fullmatch(r"\s*#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]\s*", raw) is not None


def inside_test_mod(node: tree_sitter.Node, source: bytes) -> bool:
    """Return True when *node* is inside a ``#[cfg(test)] mod …`` block."""
    current = node.parent

    while current is not None:
        if current.type == "mod_item":
            for attr in _leading_attributes(current, source):
                if _is_cfg_test_attr(attr, source):
                    return True

        current = current.parent

    return False


def _build_test_module_set(root: Path, rust_files: list[Path]) -> set[Path]:
    """Pre-pass: parse every Rust file to find ``#[cfg(test)] mod <name>;``
    declarations and map them to the corresponding file paths."""

    test_modules: set[Path] = set()

    for path in rust_files:
        source = path.read_bytes()
        tree = PARSER.parse(source)
        pending = [tree.root_node]

        while pending:
            node = pending.pop()

            if node.type == "mod_item":
                if _has_cfg_test_attribute(node, source):
                    name_node = node.child_by_field_name("name")

                    if name_node is not None:
                        mod_name = text(source, name_node)
                        test_modules.update(
                            _resolve_module_paths(path.parent, mod_name),
                        )

            pending.extend(reversed(node.named_children))

    return test_modules


def _has_cfg_test_attribute(node: tree_sitter.Node, source: bytes) -> bool:
    """Return True when *node* has a leading ``#[cfg(test)]`` attribute."""
    for attr in _leading_attributes(node, source):
        if _is_cfg_test_attr(attr, source):
            return True

    return False


def _resolve_module_paths(parent_dir: Path, mod_name: str) -> list[Path]:
    """Return possible file paths for a module declaration ``mod <name>;``
    in *parent_dir*."""

    return [
        parent_dir / f"{mod_name}.rs",
        parent_dir / mod_name / "mod.rs",
    ]


def is_test_module_file(path: Path, test_module_set: set[Path]) -> bool:
    """Return True when *path* belongs to a ``#[cfg(test)]`` module."""

    if path.name == "tests.rs":
        return True

    if "tests" in path.parts:
        return True

    if path in test_module_set:
        return True

    return False


# ---------------------------------------------------------------------------
# Error-type detection for let bindings
# ---------------------------------------------------------------------------

def _type_node_contains_word(type_node: tree_sitter.Node, source: bytes, word: str) -> bool:
    name = text(source, type_node)

    return segments_contain_word(split_identifier(name), word)


def _expr_constructs_type_word(expr: tree_sitter.Node, source: bytes, word: str) -> bool:
    """Return True when *expr* directly constructs a type containing *word*.

    Checks the RHS expression of a let-binding at the surface level:
    struct literals, call targets, macro invocations, bare identifiers,
    if/match branches.  Does NOT recurse deeply into nested blocks to
    avoid pathological performance on large files.
    """

    # --- direct Error-type naming patterns --------------------------------

    if expr.type == "struct_expression":
        name_node = expr.child_by_field_name("name")

        if name_node is not None and _type_node_contains_word(name_node, source, word):
            return True

    if expr.type == "call_expression":
        func = expr.child_by_field_name("function")

        if func is not None and _call_target_has_word(func, source, word):
            return True

    if expr.type == "field_expression":
        obj = expr.child_by_field_name("value")

        if obj is not None and _expr_constructs_type_word(obj, source, word):
            return True

    if expr.type == "macro_invocation":
        macro = expr.child_by_field_name("macro")

        if macro is not None and _type_node_contains_word(macro, source, word):
            return True

    # bare identifier (let e = SomeError;)
    if expr.type == "identifier":
        if segments_contain_word(split_identifier(text(source, expr)), word):
            return True

    # scoped path (let e = std::io::Error;)
    if expr.type in ("scoped_identifier", "scoped_type_identifier"):
        parts = text(source, expr).split("::")

        if any(segments_contain_word(split_identifier(p), word) for p in parts):
            return True

    # --- one-level look into if / match / closure for Error constructors --

    if expr.type == "if_expression":
        for child in expr.named_children:
            if child.type == "block" and _expr_constructs_type_word(child, source, word):
                return True
            if child.type == "else_clause" and _expr_constructs_type_word(child, source, word):
                return True

    if expr.type == "match_expression":
        body = expr.child_by_field_name("body")

        if body is not None and _expr_constructs_type_word(body, source, word):
            return True

    if expr.type == "match_block":
        for child in expr.named_children:
            if child.type == "match_arm":
                value = child.child_by_field_name("value")

                if value is not None and _expr_constructs_type_word(value, source, word):
                    return True

    if expr.type == "closure_expression":
        body = expr.child_by_field_name("body")

        if body is not None and _expr_constructs_type_word(body, source, word):
            return True

    if expr.type == "block":
        for child in expr.named_children:
            if child.type == "expression_statement":
                if _expr_constructs_type_word(child, source, word):
                    return True
            # direct expression in block (last expression)
            if child.type in (
                "identifier", "scoped_identifier", "call_expression",
                "struct_expression", "field_expression",
            ):
                if _expr_constructs_type_word(child, source, word):
                    return True

    if expr.type == "else_clause":
        for child in expr.named_children:
            if _expr_constructs_type_word(child, source, word):
                return True

    if expr.type == "expression_statement":
        for child in expr.named_children:
            if _expr_constructs_type_word(child, source, word):
                return True

    return False


def _named_child_by_type(
    node: tree_sitter.Node,
    child_type: str,
) -> tree_sitter.Node | None:
    """Return the first named child of *node* with the given type, or None."""
    for child in node.named_children:
        if child.type == child_type:
            return child

    return None


def _call_target_has_word(func_node: tree_sitter.Node, source: bytes, word: str) -> bool:
    if func_node.type in ("scoped_identifier", "scoped_type_identifier"):
        parts = text(source, func_node).split("::")

        # check all segments except the last (method / associated-fn name)
        for part in parts[:-1]:
            if segments_contain_word(split_identifier(part), word):
                return True

        return False

    if func_node.type == "field_expression":
        # obj.method() — only check the object (receiver), not the method name.
        # Method names like `by_error` are look-up methods, not Error constructors.
        obj = func_node.child_by_field_name("value")

        if obj is not None:
            return _expr_constructs_type_word(obj, source, word)

        return False

    if func_node.type == "identifier":
        return segments_contain_word(split_identifier(text(source, func_node)), word)

    return False


def let_binding_has_type_word(let_node: tree_sitter.Node, source: bytes, word: str) -> bool:
    """Return True when *let_node* binds to a type containing *word*.

    Checks both the explicit type annotation and the initializer expression.
    """
    type_node = let_node.child_by_field_name("type")

    if type_node is not None and _type_node_contains_word(type_node, source, word):
        return True

    value = let_node.child_by_field_name("value")

    if value is not None and _expr_constructs_type_word(value, source, word):
        return True

    return False


# ---------------------------------------------------------------------------
# identifier checking
# ---------------------------------------------------------------------------

# context tags passed alongside each collected name
CTX_FUNCTION       = "function"
CTX_LET            = "let"
CTX_PARAMETER      = "parameter"
CTX_CONST          = "const"
CTX_STATIC         = "static"
CTX_ENUM_VARIANT   = "enum_variant"
CTX_TYPE           = "type"
CTX_FIELD          = "field"
CTX_MACRO_FIELD    = "macro_field"


def check_identifier_name(
    name: str,
    name_node: tree_sitter.Node,
    context: str,
    is_error_type: bool,
    path: Path,
    root: Path,
    violations: list[Violation],
    module: str,
    forbidden_segments: dict[str, dict],
    prefixes: list[tuple[str, str, str, frozenset[str]]],
    contextual_rule: dict,
) -> None:
    """Check a single identifier for forbidden word segments."""

    for prefix, code, message, contexts in prefixes:
        if name.startswith(prefix) and context in contexts:
            violations.append(
                error_line(
                    path,
                    root,
                    name_node,
                    code,
                    message.format(name=name, prefix=prefix, context=context),
                ),
            )
            return

    segments = split_identifier(name)

    word = str(contextual_rule.get("word", ""))
    contexts = frozenset(str(value) for value in contextual_rule.get("contexts", []))

    if word and word in segments and context in contexts:
        word_index = segments.index(word)
        last = len(segments) - 1
        code = str(contextual_rule["code"])

        if context == CTX_FUNCTION:
            allowed = contextual_rule.get("function_allowed_position")

            if allowed != "suffix" or word_index != last or len(segments) == 1:
                violations.append(
                    error_line(
                        path, root, name_node,
                        code,
                        str(contextual_rule["function_message"]).format(
                            name=name, word=word, context=context,
                        ),
                    ),
                )
                return
            return

        if context in (CTX_LET, CTX_PARAMETER):
            allowed = contextual_rule.get("local_allowed_position")

            if allowed == "prefix" and word_index == 0 and len(segments) >= 2 and not is_error_type:
                return
            violations.append(
                error_line(
                    path,
                    root,
                    name_node,
                    code,
                    str(contextual_rule["local_message"]).format(
                        name=name, word=word, context=context,
                    ),
                ),
            )
            return

        violations.append(
            error_line(
                path, root, name_node,
                code,
                str(contextual_rule["other_message"]).format(
                    name=name, word=word, context=context,
                ),
            ),
        )
        return

    for segment in segments:
        if segment in forbidden_segments:
            rule = forbidden_segments[segment]
            contexts = rule["contexts"]
            allowed_modules = rule["allowed_modules"]

            if context in contexts and not module_is_allowed(module, allowed_modules):
                replacement = rule["replacement"]
                message = (
                    f"'{name}' uses '{segment}'; use '{replacement}' instead"
                    if replacement
                    else f"'{name}' contains forbidden word '{segment}'"
                )
                violations.append(
                    error_line(
                        path, root, name_node,
                        rule["code"],
                        message,
                    ),
                )
            return


# ---------------------------------------------------------------------------
# tree walking — collect definition-site names with context
# ---------------------------------------------------------------------------

def collect_definition_names(
    node: tree_sitter.Node,
    source: bytes,
    names: list[tuple[str, tree_sitter.Node, str, bool, tuple[str, ...]]],
    error_type_word: str,
    current_module: tuple[str, ...],
) -> None:
    """Walk *node* recursively and collect every user-defined identifier name
    together with its tree-sitter node, context tag, and Error-type flag."""

    if node.type == "mod_item" and node.child_by_field_name("body") is not None:
        name_node = node.child_by_field_name("name")

        if name_node is not None:
            current_module += (text(source, name_node),)

    # --- declaration `name` field ---
    if node.type in DECLARATION_KINDS:
        name_node = node.child_by_field_name("name")

        if name_node is not None:
            if node.type in ("function_item", "function_signature_item"):
                ctx = CTX_FUNCTION
            elif node.type == "const_item":
                ctx = CTX_CONST
            elif node.type == "static_item":
                ctx = CTX_STATIC
            elif node.type == "enum_variant":
                ctx = CTX_ENUM_VARIANT
            elif node.type in (
                "struct_item", "enum_item", "type_item",
                "trait_item", "union_item",
            ):
                ctx = CTX_TYPE
            elif node.type == "field_declaration":
                ctx = CTX_FIELD
            else:
                ctx = CTX_LET  # unreachable

            names.append((text(source, name_node), name_node, ctx, False, current_module))

    # --- function / closure parameter ---
    if node.type == "parameter":
        for child in node.named_children:
            if child.type == "identifier":
                names.append((text(source, child), child, CTX_PARAMETER, False, current_module))
                break

    # --- structured macro field key ---
    if node.type == "macro_invocation":
        token_tree = next(
            (
                child
                for child in node.named_children
                if child.type == "token_tree"
            ),
            None,
        )

        if token_tree is not None:
            children = token_tree.children

            for index, child in enumerate(children[:-1]):
                next_child = children[index + 1]

                if (
                    child.type == "identifier"
                    and next_child.text == b"="
                ):
                    names.append((text(source, child), child, CTX_MACRO_FIELD, False, current_module))

    # --- let binding (pattern → identifier, with Error-type detection) ---
    if node.type == "let_declaration":
        pattern = node.child_by_field_name("pattern")

        if pattern is not None:
            is_err_type = bool(error_type_word) and let_binding_has_type_word(
                node,
                source,
                error_type_word,
            )
            _collect_pattern_identifiers(
                pattern, source, names, CTX_LET, is_err_type, current_module,
            )

    # --- for-loop variable ---
    if node.type == "for_expression":
        pattern = node.child_by_field_name("pattern")

        if pattern is not None:
            _collect_pattern_identifiers(
                pattern, source, names, CTX_LET, False, current_module,
            )

    # --- match arm binding ---
    if node.type == "match_pattern":
        _collect_pattern_identifiers(
            node, source, names, CTX_LET, False, current_module,
        )

    # recurse
    for child in node.named_children:
        collect_definition_names(
            child, source, names, error_type_word, current_module,
        )


def _collect_pattern_identifiers(
    pattern: tree_sitter.Node,
    source: bytes,
    names: list[tuple[str, tree_sitter.Node, str, bool, tuple[str, ...]]],
    context: str,
    is_error_type: bool,
    current_module: tuple[str, ...],
) -> None:
    if pattern.type == "identifier":
        names.append((
            text(source, pattern), pattern, context, is_error_type, current_module,
        ))
        return

    if pattern.type in ("tuple_pattern", "tuple_struct_pattern"):
        for child in pattern.named_children:
            _collect_pattern_identifiers(
                child, source, names, context, is_error_type, current_module,
            )
        return

    if pattern.type == "struct_pattern":
        for child in pattern.named_children:
            if child.type == "field_pattern":
                pattern_child = child.child_by_field_name("pattern")

                if pattern_child is not None:
                    _collect_pattern_identifiers(
                        pattern_child, source, names, context, is_error_type, current_module,
                    )
                else:
                    field_name = child.child_by_field_name("name")

                    if field_name is not None:
                        names.append((
                            text(source, field_name), field_name, context,
                            is_error_type, current_module,
                        ))
        return

    if pattern.type == "or_pattern":
        for child in pattern.named_children:
            _collect_pattern_identifiers(
                child, source, names, context, is_error_type, current_module,
            )
        return

    if pattern.type in ("ref_pattern", "mutable_pattern"):
        sub = pattern.named_children[0] if pattern.named_children else None

        if sub is not None:
            _collect_pattern_identifiers(
                sub, source, names, context, is_error_type, current_module,
            )
        return


def is_skipped_module_path(path: Path, root: Path, config: dict) -> bool:
    """Return True for files under an exempted path segment."""
    skip_paths = config.get("skip_module_paths", [])

    if not skip_paths:
        return False

    relative = str(path.relative_to(root))

    return any(segment in relative for segment in skip_paths)


# ---------------------------------------------------------------------------
# per-file entry point
# ---------------------------------------------------------------------------

def check_file(
    path: Path,
    root: Path,
    test_module_set: set[Path],
    forbidden_segments: dict[str, dict],
    prefixes: list[tuple[str, str, str, frozenset[str]]],
    contextual_rule: dict,
    allowed_modules: frozenset[str],
    config: dict,
) -> list[Violation]:
    source = production_source(path, root)
    violations: list[Violation] = []

    # entire file is a test module — skip identifier checks
    if is_test_module_file(path, test_module_set):
        return violations

    # exempted module-path files are skipped entirely
    if is_skipped_module_path(path, root, config):
        return violations

    names: list[tuple[str, tree_sitter.Node, str, bool, tuple[str, ...]]] = []

    error_type_word = str(contextual_rule.get("error_type_word", ""))
    collect_definition_names(
        PARSER.parse(source).root_node,
        source,
        names,
        error_type_word,
        file_module(path, root),
    )

    for name, name_node, context, is_error_type, module_parts in names:
        if inside_test_mod(name_node, source):
            continue

        module = format_module(module_parts)

        if module_is_allowed(module, allowed_modules):
            continue

        check_identifier_name(
            name,
            name_node,
            context,
            is_error_type,
            path,
            root,
            violations,
            module,
            forbidden_segments,
            prefixes,
            contextual_rule,
        )

    return violations


def configured_segments(config: dict) -> dict[str, dict]:
    segments: dict[str, dict] = {}

    for entry in config.get("words", []):
        segments[str(entry["word"])] = {
            "code": str(entry["code"]),
            "replacement": str(entry.get("replacement", "")),
            "contexts": frozenset(
                str(value) for value in entry.get("contexts", [])
            ),
            "allowed_modules": frozenset(
                str(value) for value in entry.get("allowed_modules", [])
            ),
        }

    return segments


def configured_prefixes(config: dict) -> list[tuple[str, str, str, frozenset[str]]]:
    return [
        (
            str(entry["prefix"]),
            str(entry["code"]),
            str(entry["message"]),
            frozenset(str(value) for value in entry.get("contexts", [])),
        )
        for entry in config.get("prefixes", [])
    ]


def check(root: Path, config: dict | None = None) -> list[Violation]:
    """Return forbidden-identifier violations under src/."""
    root = root.resolve()
    section = merged("forbidden-identifiers", config)
    ignored_files: set[Path] = set()
    configured_ignores = section.get("ignore_files", [])

    if configured_ignores:
        ignored_files = resolve_ignore_files(root, [Path(item) for item in configured_ignores], [])

    files = [
        path
        for path in rust_files(root, section)
        if path.resolve() not in ignored_files
    ]
    test_module_set = _build_test_module_set(root, files)
    forbidden_segments = configured_segments(section)
    prefixes = configured_prefixes(section)
    contextual_rule = dict(section.get("contextual_word", {}))
    allowed_modules = frozenset(
        str(value) for value in section.get("allowed_modules", [])
    )

    return [
        violation
        for path in files
        for violation in check_file(
            path,
            root,
            test_module_set,
            forbidden_segments,
            prefixes,
            contextual_rule,
            allowed_modules,
            section,
        )
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

        # ── valid: nothing forbidden ────────────────────────────────────

        fixture.write_text(
            "fn process_input(data: &[u8]) -> Vec<u8> {\n"
            "    let processed = Vec::new();\n"
            "    let err_msg = \"something\";\n"       # err_ prefix, not Error type
            "    let prev_value = 1;\n"
            "    for byte in data {\n"
            "        match byte {\n"
            "            0 => continue,\n"
            "            _ => processed.push(*byte),\n"
            "        }\n"
            "    }\n"
            "    processed\n"
            "}\n"
            "\n"
            "fn parse_err() -> Result<(), ()> { Ok(()) }\n"  # _err suffix in fn
            "\n"
            "pub struct ConnInfo {\n"
            "    pub db_conn: String,\n"
            "}\n"
            "\n"
            "const MAX_RETRIES: u32 = 3;\n"
            "static GLOBAL_CONFIG: &str = \"\";\n"
        )

        if check(root):
            print("self-test: valid fixture was rejected", file=sys.stderr)
            for violation in check(root):
                print(f"  {violation}", file=sys.stderr)
            return 1

        ignored_file = src / "ignored.rs"
        ignored_file.write_text("fn ignored_result() {}\n")

        if check(root, {"ignore_files": ["src/ignored.rs"]}):
            print("self-test: ignored file was not checked", file=sys.stderr)
            return 1

        ignore_list = root / "ignore-files.txt"
        ignore_list.write_text(
            "# Paths are relative to the check root.\n"
            "src/ignored.rs\n"
        )

        if check(root, {"ignore_files": ["src/ignored.rs"]}):
            print("self-test: ignore list was not applied", file=sys.stderr)
            return 1

        ignored_file.unlink()

        # ── test module is skipped ──────────────────────────────────────

        fixture.write_text(
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    fn helper() {\n"
            "        let result = 1;\n"
            "        let error = \"bad\";\n"
            "        let err = SomeError;\n"
            "    }\n"
            "}\n"
        )

        if check(root):
            print("self-test: test module was not skipped", file=sys.stderr)
            return 1

        # ── error → always forbidden (FBD003) ───────────────────────────

        fixture.write_text(
            "fn handle_error() {}\n"               # fn name
            "fn process() {\n"
            "    let error_msg = \"\";\n"           # let binding
            "    let error = MyError;\n"            # bare error variable
            "}\n"
            "const MAX_ERROR: u32 = 0;\n"           # const
            "static ERROR_CODE: u32 = 0;\n"         # static
        )

        violations = check(root)

        if len(violations) != 5:
            print(
                f"self-test FBD003: expected 5 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        if not all(violation.code == "FBD003" for violation in violations):
            print("self-test FBD003: wrong code", file=sys.stderr)
            return 1

        # ── error in structured macro field keys ────────────────────────

        fixture.write_text(
            "fn process() {\n"
            "    tracing::warn!(\n"
            "        error_variant = ?SomeError,\n"
            "        err_message = %message,\n"
            "        \"failed\",\n"
            "    );\n"
            "}\n"
        )

        violations = check(root)

        if len(violations) != 1 or "error_variant" not in violations[0].message:
            print(
                "self-test FBD003-macro-field: error_variant was not flagged",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        # ── err in fn names: only _err suffix allowed ────────────────────

        fixture.write_text(
            "fn parse_err() {}     // allowed (_err suffix)\n"
            "fn err_handler() {}   // forbidden (err_ prefix in fn)\n"
            "fn do_err() {}        // allowed (_err suffix)\n"
            "fn err() {}           // forbidden (bare err)\n"
        )

        violations = check(root)
        # parse_err → allowed, err_handler → FBD004, do_err → allowed, err → FBD004

        if len(violations) != 2:
            print(
                f"self-test FBD004-fn: expected 2 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        flagged_names = {
            violation.message.split("'")[1]
            for violation in violations
            if violation.code == "FBD004"
        }

        if "err_handler" not in flagged_names:
            print("self-test FBD004-fn: err_handler not flagged", file=sys.stderr)
            return 1

        if "err" not in flagged_names:
            print("self-test FBD004-fn: bare err fn not flagged", file=sys.stderr)
            return 1

        # ── err in local vars: err_ prefix + not Error type = allowed ────

        fixture.write_text(
            "fn process() {\n"
            "    let err_code: u32 = 5;\n"          # allowed (err_ prefix, not Error type)
            "    let err_msg = String::new();\n"     # allowed (err_ prefix, not Error type)
            "    let err = 42;\n"                    # forbidden (bare err)
            "    let parse_err = 42;\n"              # forbidden (_err suffix in var)
            "}\n"
        )

        violations = check(root)
        # err → FBD004, parse_err → FBD004

        if len(violations) != 2:
            print(
                f"self-test FBD004-let-basic: expected 2 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        # ── err in local vars: Error type instantiation → always forbidden

        fixture.write_text(
            "struct SomeError;\n"
            "fn process() {\n"
            "    let err_code = SomeError;\n"       # forbidden (err_ prefix BUT Error type)
            "    let err = SomeError;\n"            # forbidden (bare err + Error type)
            "    let parse_err = SomeError;\n"      # forbidden (_err suffix + Error type)
            "}\n"
        )

        violations = check(root)
        # err_code (Error type) → FBD004, err (Error type) → FBD004, parse_err (Error type) → FBD004

        if len(violations) != 3:
            print(
                f"self-test FBD004-let-error-type: expected 3 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        # ── err: Error type from function call ──────────────────────────

        fixture.write_text(
            "struct SomeError;\n"
            "impl SomeError { fn new() -> Self { Self } }\n"
            "fn process() {\n"
            "    let err_msg = SomeError::new();\n"    # forbidden (err_ prefix + Error type via call)
            "    let e = create_error();\n"             # FBD003 (error in fn name, not err check)
            "}\n"
            "fn create_error() -> SomeError { SomeError }\n"
        )

        violations = check(root)
        # err_msg → FBD004 (Error type via SomeError::new())
        # create_error (fn def) → FBD003

        if len(violations) != 2:
            print(
                f"self-test FBD004-call: expected 2 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        # ── err: Error type from struct expression ──────────────────────

        fixture.write_text(
            "struct ParseError { code: u32 }\n"
            "fn process() {\n"
            "    let err_info = ParseError { code: 1 };\n"  # forbidden (err_ prefix + Error type via struct expr)
            "}\n"
        )

        violations = check(root)
        # err_info → FBD004, ParseError → NOT checked (type name)

        if len(violations) != 1:
            print(
                f"self-test FBD004-struct-expr: expected 1 violation, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        # ── err: Error type inside if/match/closure/field-call ──────────

        fixture.write_text(
            "struct SomeError;\n"
            "struct OtherError;\n"
            "impl SomeError { fn new() -> Self { Self } }\n"
            "impl Helper {\n"
            "    fn create_error() -> SomeError { SomeError }\n"
            "}\n"
            "fn process(cond: bool) {\n"
            "    let err_val = if cond { SomeError } else { OtherError };\n"
            "    let err_out = match cond { true => SomeError::new(), _ => OtherError };\n"
            "    let err_fn = || SomeError::new();\n"
            "}\n"
        )

        violations = check(root)
        # err_val (err_ prefix + Error type via if) → FBD004
        # err_out (err_ prefix + Error type via match) → FBD004
        # err_fn (err_ prefix + Error type via closure) → FBD004
        # create_error fn def (in impl Helper) → FBD003

        if len(violations) != 4:
            print(
                f"self-test FBD004-compound: expected 4 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        fbd004_names = {
            violation.message.split("'")[1]
            for violation in violations
            if violation.code == "FBD004"
        }

        if fbd004_names != {"err_val", "err_out", "err_fn"}:
            print(
                f"self-test FBD004-compound: wrong names flagged: {fbd004_names}",
                file=sys.stderr,
            )
            return 1

        # ── parameter with err ──────────────────────────────────────────

        fixture.write_text(
            "fn process(err_code: u32) {}\n"        # allowed (err_ prefix, not Error type)
            "fn process2(err: u32) {}\n"             # forbidden (bare err)
            "fn process3(parse_err: u32) {}\n"       # forbidden (_err suffix in param)
        )

        violations = check(root)
        # err → FBD004, parse_err → FBD004

        if len(violations) != 2:
            print(
                f"self-test FBD004-param: expected 2 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        # ── err in const / static → forbidden ──────────────────────────

        fixture.write_text(
            "const ERR_CODE: u32 = 0;\n"
            "static GLOBAL_ERR: u32 = 0;\n"
        )

        violations = check(root)
        # ERR_CODE → FBD004, GLOBAL_ERR → FBD004

        if len(violations) != 2:
            print(
                f"self-test FBD004-other: expected 2 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        # ── other forbidden segments ────────────────────────────────────

        fixture.write_text(
            "fn parse_result() {}\n"
            "fn compute_res() {}\n"
            "fn get_closure() {}\n"
            "fn open_connection() {}\n"
            "fn begin_txn() {}\n"
            "fn commit_tx() {}\n"
            "static target_name: &str = \"\";\n"
            "fn read_previous() {}\n"
            "fn read_prev() {}\n"
            "fn read_Previous() {}\n"
        )

        violations = check(root)
        # 8 forbidden function names + 1 static = 9, with read_prev allowed.

        if len(violations) != 9:
            print(
                f"self-test other codes: expected 9 violations, got {len(violations)}",
                file=sys.stderr,
            )
            for violation in violations:
                print(f"  {violation}", file=sys.stderr)
            return 1

        # ── FBD code coverage ───────────────────────────────────────────

        fixture.write_text(
            "fn f1(result: ()) {}\n"         # FBD001
            "fn f2(res: ()) {}\n"            # FBD002
            "fn f3(error: ()) {}\n"          # FBD003
            "fn f4(err: ()) {}\n"            # FBD004
            "fn f5(closure: ()) {}\n"        # FBD005
            "fn f6(connection: ()) {}\n"     # FBD006
            "fn f7(txn: ()) {}\n"            # FBD007
            "fn f8(tx: ()) {}\n"             # FBD008
            "static target_x: u8 = 0;\n"     # FBD009
            "fn f9(extension: ()) {}\n"      # FBD010
            "fn f10(previous_value: ()) {}\n" # FBD011
            "fn f11(PreviousValue: ()) {}\n" # FBD011 via PascalCase
            "fn f12(replacements: ()) {}\n" # FBD012
            "fn f13(current: ()) {}\n"      # FBD013
            "fn f14(prev_value: (), repl: (), curr: (), msg: ()) {}\n" # allowed replacements
        )

        violations = check(root)
        codes = {violation.code for violation in violations}

        expected_codes = {f"FBD{i:03d}" for i in range(1, 14)}

        if codes != expected_codes:
            missing = expected_codes - codes
            extra = codes - expected_codes
            print(
                f"self-test: code coverage — missing {missing}, extra {extra}",
                file=sys.stderr,
            )
            return 1

    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(prog="forbidden-identifiers")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--ignore-file",
        dest="ignore_files",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="skip one Rust source file; may be repeated",
    )
    parser.add_argument(
        "--ignore-list",
        dest="ignore_lists",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help="read newline-delimited ignored paths; may be repeated",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = args.root.resolve()
    section = merged("forbidden-identifiers", None)
    ignored_files = resolve_ignore_files(root, args.ignore_files, args.ignore_lists)
    files = [
        path
        for path in rust_files(root, section)
        if path.resolve() not in ignored_files
    ]
    test_module_set = _build_test_module_set(root, files)
    forbidden_segments = configured_segments(section)
    prefixes = configured_prefixes(section)
    contextual_rule = dict(section.get("contextual_word", {}))
    allowed_modules = frozenset(
        str(value) for value in section.get("allowed_modules", [])
    )
    violations = [
        violation
        for path in files
        for violation in check_file(
            path,
            root,
            test_module_set,
            forbidden_segments,
            prefixes,
            contextual_rule,
            allowed_modules,
            section,
        )
    ]

    for violation in violations:
        print(f"{violation.path}:{violation.line}: {violation.code}: {violation.message}", file=sys.stderr)

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
