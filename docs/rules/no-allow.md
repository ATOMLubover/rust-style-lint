# no-allow

> Forbid all lint suppression attributes: `#[allow(...)]` / `#[expect(...)]`.
> Code: `NO_ALLOW` | `--fix`: unsupported (check only)

## Goal

Suppressions (`allow` / `expect`) hide real problems and decay as code evolves. The **only** way to remove a suppression is to refactor the code so the lint no longer fires:

- Unused conditional imports -> use `#[cfg(feature = "...")] use ...;` for types used only in swagger macro attributes.
- `dead_code` -> delete dead code, or mark genuinely needed items `pub`.
- Too many function arguments -> group them in a struct / builder.
- Apply the same principle to other lints.

Other attributes are unaffected: `#[cfg(...)]`, `#[derive(...)]`, `#[cfg_attr(..., derive(...))]`, `#[deprecated]`, etc.

## Trigger

Any `#[allow(...)]` or `#[expect(...)]` triggers a diagnostic that includes the suppressed lint name.

```rust
// BAD - Suppressing an unused import
#[allow(unused_imports)]
use crate::data::val::chapter_port::ExportChapterTranslationVal;

// GOOD - Import only when swagger is enabled; the macro attribute uses it in that configuration.
#[cfg(feature = "swagger")]
use crate::data::val::chapter_port::ExportChapterTranslationVal;

// BAD - Suppressing dead code
#[allow(dead_code)]
fn helper() {}

// GOOD - Delete it or make callers actually use it.
```

## Implementation

For each `.rs` file, use tree-sitter to find all `attribute_item` nodes and match
`#\[(allow|expect)\(...\)\]` with a regular expression. Each match reports `NO_ALLOW`.

**Not fixable**: `--fix` is a no-op; refactor the code manually.
