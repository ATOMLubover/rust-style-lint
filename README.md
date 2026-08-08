# rust-style-lint

Tree-sitter based Rust style checkers, selected and configured per project
via a `rust-style-lint.toml` at the project root. Extracted from the
PopRaKo `fmt/` suite: every checker is a pure AST pass — reports violations
only, never modifies source (except checkers that document `--fix`).

## Rule documentation

Every rule has a dedicated spec in `docs/rules/` — goal, trigger condition, exact
violation message, GOOD/BAD examples, `--fix` support, and config keys. Read the
doc instead of the source when a rule flags your code:

| Name | Code | Doc |
| --- | --- | --- |
| `doc-comment-coverage` | DOC001 | [doc-comment-coverage.md](docs/rules/doc-comment-coverage.md) |
| `forbidden-identifiers` | FBD001-011 | [forbidden-identifiers.md](docs/rules/forbidden-identifiers.md) |
| `generic-where` | GEN001-002 | [generic-where.md](docs/rules/generic-where.md) |
| `item-layout` | LAYOUT001-004 | [item-layout.md](docs/rules/item-layout.md) |
| `module-dependency` | MOD001-002 | [module-dependency.md](docs/rules/module-dependency.md) |
| `no-inline-tests` | TST001 | [no-inline-tests.md](docs/rules/no-inline-tests.md) |
| `no-type-hint` | NO_TYPE_HINT | [no-type-hint.md](docs/rules/no-type-hint.md) |
| `spacing-style` | BLK000-002, PARSE001 | [spacing-style.md](docs/rules/spacing-style.md) |
| `trait-use-anonymous` | TRAIT001 | [trait-use-anonymous.md](docs/rules/trait-use-anonymous.md) |
| `use-style` | USE_\* (14 rules) | [use-style.md](docs/rules/use-style.md) |
| `visibility-style` | VIS001-002 | [visibility-style.md](docs/rules/visibility-style.md) |

## Checkers

### No configuration needed

| Name | Code | Rule |
| --- | --- | --- |
| `no-inline-tests` | TST001 | `#[cfg(test)] mod tests { … }` must live in a separate `tests.rs` |
| `spacing-style` | BLK000-002 | block-start `//` separator, blank lines between direct statements / match arms / enum variants |
| `use-style` | USE_\* | import grouping, merging, sorting, dedup; `as _` for known traits; `--fix` capable |
| `generic-where` | GEN001-002 | bounds in `where` clauses; no argument-position `impl Trait` |
| `no-type-hint` | NO_TYPE_HINT | no `let x: T = …`; pin types with turbofish |
| `item-layout` | LAYOUT001-004 | impl follows struct; pub before private; helpers in first-call order |

### Configurable

| Name | Code | Config keys |
| --- | --- | --- |
| `doc-comment-coverage` | DOC001 | `exclude_segments`, `exclude_filename_prefixes`, `exclude_filenames` |
| `forbidden-identifiers` | FBD001-011 | `words` (extra forbidden segments), `skip_module_paths`, `ignore_files`, `exclude_filenames` |
| `module-dependency` | MOD001-002 | `exclude_files` |
| `visibility-style` | VIS001-002 | `allow_public_fields`, `exclude_files` |
| `trait-use-anonymous` | TRAIT001 | `external_traits`, `macro_traits`, `macro_markers` |
| `prefer-if-let-guard` | LET001 | `diverging_macros`, `exclude_segments`, `exclude_filename_prefixes`, `exclude_filenames` |

## Usage

Put a `rust-style-lint.toml` at the target project root:

```toml
[linters]
no-inline-tests = true
use-style = true
# … only the checkers you want; commented-out keys are disabled

[module-dependency]
exclude_files = ["src/generated/schema.rs"]
```

### Configuration semantics

Rule tables are data, not code. The package ships `defaults.toml` with every
default table (forbidden words, known traits, exclusions, ignore directories).
A project section **replaces the packaged defaults for that checker as a
whole** — a `[forbidden-identifiers]` section therefore declares the complete
word table, not a delta. Sections that are absent fall back to the packaged
defaults. There is no rule content in the checker code itself.

Run from anywhere:

```sh
./run-check.sh --root /path/to/target
# or with a checked-in Python environment:
python -m rust_style_lint --root /path/to/target
```

Flags:

- `--root DIR` — target project root (default: current directory).
- `--fix` — call each checker's `fix(root, config)` where one exists.
- `--self-test` — run every checker's internal self-test.

Checkers can also run standalone:

```sh
python -m rust_style_lint.checkers.use_style --root /path/to/target
python -m rust_style_lint.checkers.forbidden_identifiers --root . --self-test
```

## Checker contract

Every module in `rust_style_lint/checkers/` exposes:

```python
def check(root: Path, config: dict | None = None) -> list[Violation]: ...
def fix(root: Path, config: dict | None = None) -> list[Violation]: ...  # optional
def self_test() -> int: ...                                              # optional
```

`config` is the checker's `[name]` TOML section, or `None`. `Violation` is
`(path, line, column | None, code, message)` with `path` relative to `root`.

## Development

```sh
uv venv .venv --seed
uv pip sync requirements.txt --python .venv/bin/python
.venv/bin/python -m rust_style_lint --self-test
.venv/bin/python -m unittest tests.test_spacing_style
```
