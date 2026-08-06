"""Return Rust source with test-only modules masked for fmt checkers."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import tree_sitter
import tree_sitter_rust


PARSER = tree_sitter.Parser(tree_sitter.Language(tree_sitter_rust.language()))
_CFG = re.compile(r"\s*#\s*\[\s*cfg\s*\((.*)\)\s*]\s*", re.DOTALL)
_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*|[(),=]')


def _text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode()


def _leading_attributes(node: tree_sitter.Node) -> list[tree_sitter.Node]:
    parent = node.parent

    if parent is None:
        return []

    siblings = parent.children
    index = next((index for index, child in enumerate(siblings) if child.id == node.id), None)

    if index is None:
        return []

    attributes: list[tree_sitter.Node] = []

    for sibling in reversed(siblings[:index]):
        if sibling.type == "attribute_item":
            attributes.append(sibling)
            continue

        if sibling.type in {"line_comment", "block_comment"}:
            continue

        break

    return attributes


class _CfgParser:
    """Evaluate possible cfg values after fixing the `test` flag to false."""

    def __init__(self, expression: str) -> None:
        self.tokens = _TOKEN.findall(expression)
        self.index = 0

    def parse(self) -> set[bool]:
        values = self._expression()

        return values if self.index == len(self.tokens) else {True, False}

    def _expression(self) -> set[bool]:
        if self.index >= len(self.tokens):
            return {True, False}

        token = self.tokens[self.index]
        self.index += 1

        if token == "test":
            return {False}

        if self.index < len(self.tokens) and self.tokens[self.index] == "=":
            self.index += 2
            return {True, False}

        if self.index >= len(self.tokens) or self.tokens[self.index] != "(":
            return {True, False}

        self.index += 1
        values: list[set[bool]] = []

        while self.index < len(self.tokens) and self.tokens[self.index] != ")":
            values.append(self._expression())

            if self.index < len(self.tokens) and self.tokens[self.index] == ",":
                self.index += 1

        if self.index < len(self.tokens):
            self.index += 1

        products = {()}

        for value in values:
            products = {prefix + (item,) for prefix in products for item in value}

        if token == "all":
            return {all(product) for product in products}

        if token == "any":
            return {any(product) for product in products}

        if token == "not":
            return {not item for product in products for item in product}

        return {True, False}


def _has_test_only_cfg(node: tree_sitter.Node, source: bytes) -> bool:
    for attribute in _leading_attributes(node):
        match = _CFG.fullmatch(_text(source, attribute))

        if match is not None and True not in _CfgParser(match.group(1)).parse():
            return True

    return False


def _module_paths(path: Path, name: str) -> tuple[Path, ...]:
    parents = [path.parent]

    if path.name not in {"lib.rs", "main.rs", "mod.rs"}:
        parents.append(path.parent / path.stem)

    return tuple(candidate for parent in parents for candidate in (parent / f"{name}.rs", parent / name / "mod.rs"))


@lru_cache(maxsize=None)
def _test_module_files(root: Path) -> frozenset[Path]:
    files = tuple((root / "src").rglob("*.rs"))
    test_files = {path.resolve() for path in files if path.name == "tests.rs" or "tests" in path.parts}

    for path in files:
        source = path.read_bytes()
        pending = [PARSER.parse(source).root_node]

        while pending:
            node = pending.pop()

            if node.type == "mod_item" and _has_test_only_cfg(node, source):
                name = node.child_by_field_name("name")

                if name is not None and node.child_by_field_name("body") is None:
                    test_files.update(
                        candidate.resolve()
                        for candidate in _module_paths(path, _text(source, name))
                        if candidate.is_file()
                    )

            pending.extend(reversed(node.named_children))

    return frozenset(test_files)


def _mask(source: bytes, start: int, end: int) -> bytes:
    return source[:start] + bytes(10 if byte == 10 else 13 if byte == 13 else 32 for byte in source[start:end]) + source[end:]


def production_source(path: Path, root: Path) -> bytes:
    """Mask modules unreachable when Rust compiles without `cfg(test)`."""
    source = path.read_bytes()

    root = root.resolve()

    if path.resolve() in _test_module_files(root):
        return _mask(source, 0, len(source))

    tree = PARSER.parse(source)
    ranges: list[tuple[int, int]] = []
    pending = [tree.root_node]

    while pending:
        node = pending.pop()

        if node.type == "mod_item" and _has_test_only_cfg(node, source):
            attributes = _leading_attributes(node)
            start = min((attribute.start_byte for attribute in attributes), default=node.start_byte)
            ranges.append((start, node.end_byte))
            continue

        pending.extend(reversed(node.named_children))

    for start, end in reversed(ranges):
        source = _mask(source, start, end)

    return source
