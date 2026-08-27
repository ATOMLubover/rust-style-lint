"""Load the package default rule tables and merge per-checker project config.

The package ships `defaults.toml` with every rule table (forbidden words,
known traits, exclusions, and so on) so that the checker code contains no
rule content. A project's `rust-style-lint.toml` overrides a checker
section as a whole: when the project defines `[checker-name]`, that section
replaces the defaults for that checker entirely.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path


DEFAULTS_PATH = Path(__file__).with_name("defaults.toml")


@lru_cache(maxsize=1)
def load_defaults() -> dict:
    with DEFAULTS_PATH.open("rb") as handle:
        return tomllib.load(handle)


def merged(name: str, config: dict | None) -> dict:
    """Return the effective config section for *name*.

    The project section replaces the defaults entirely. Only ``None`` means
    that the project section is absent; an explicitly empty section disables
    every configured rule in that section.
    """
    if config is not None:
        return config

    return dict(load_defaults().get(name, {}))
