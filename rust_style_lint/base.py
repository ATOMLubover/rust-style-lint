"""Shared checker contracts: violations, root resolution, file discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    code: str
    message: str
    column: int | None = None
    # "error" fails the run; "warning" is reported but does not fail it.
    level: str = "error"


def source_files(root: Path, subdir: str = "src") -> list[Path]:
    """Return sorted production `.rs` files under root/<subdir>."""
    return sorted((root / subdir).rglob("*.rs"))


def resolve_relative(root: Path, value: str) -> Path:
    return (value if Path(value).is_absolute() else root / value).resolve()
