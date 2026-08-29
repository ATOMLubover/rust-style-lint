"""Shared checker contracts: violations, root resolution, file discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


IGNORED_SOURCE_PARENTS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "target",
})


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    code: str
    message: str
    column: int | None = None
    # "error" fails the run; "warning" is reported but does not fail it.
    level: str = "error"


def source_dirs(root: Path) -> list[Path]:
    """Return every Rust source directory below a project/workspace root."""
    return sorted(
        path
        for path in root.rglob("src")
        if path.is_dir()
        and not any(part in IGNORED_SOURCE_PARENTS for part in path.relative_to(root).parts)
    )


def source_root(path: Path, root: Path) -> Path:
    """Return the closest enclosing `src` directory for a source file."""
    current = path.parent

    while current != root and root in current.parents:
        if current.name == "src":
            return current

        current = current.parent

    if current.name == "src":
        return current

    return root / "src"


def crate_roots(root: Path) -> list[Path]:
    """Return crate roots represented by source directories in the project."""
    return [path.parent for path in source_dirs(root)]


def is_test_source(path: Path, root: Path) -> bool:
    """Return whether a source path is conventionally a test-only file."""
    relative = path.relative_to(source_root(path, root))
    return path.name == "tests.rs" or "tests" in relative.parts


def source_files(root: Path, include_tests: bool = False) -> list[Path]:
    """Return Rust files from every `src` below a project/workspace root."""
    files = (path for directory in source_dirs(root) for path in directory.rglob("*.rs"))

    if include_tests:
        return sorted(set(files))

    return sorted({path for path in files if not is_test_source(path, root)})


def resolve_relative(root: Path, value: str) -> Path:
    return (value if Path(value).is_absolute() else root / value).resolve()
