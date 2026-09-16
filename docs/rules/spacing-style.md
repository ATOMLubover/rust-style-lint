# spacing-style

> Custom Rust block spacing: leading separators and blank lines between statements, match arms, enum variants, and module items.
> Codes: `BLK000`-`BLK003`, `PARSE001` | `--fix`: supported for BLK000/BLK001/BLK002/BLK003

## Goal

Enforce consistent layout:

1. Blocks containing multiple statements or match arms require a bare `//` separator after the opening `{`.
2. Adjacent statements, match arms, and enum variants require a blank line between them.
3. Bare `//` is forbidden at the start of struct/enum declarations, struct literals, and field lists; it is redundant in compact single-unit blocks.
4. Module-scope items (struct/impl/trait/fn/mod/use/const/static/type/etc.) require blank lines between them.

## What is a bare `//` separator?

A line matching `^\s*//\s*$`: only `//`, with optional surrounding whitespace. It is purely a visual separator.
A real comment, in contrast, contains text, such as `// Explanation`.

A real comment at the start of a function or match block satisfies BLK000. Bare `//` is forbidden at the start of struct/enum declarations;
keep `///` or `//` comments containing text.

## BLK000 - Missing leading bare `//` separator

> Message: `{description} whose opening brace is not on its own line requires a bare // separator before its first {kind}`
> `description`: `multi-{kind} block` (at least two units) or `multi-line {kind} block` (one unit spanning multiple lines).
> `kind`: `statement` or `match arm`.

### Trigger conditions

The container is a `block` or `match_block`, and:

- `{` is not on a line by itself; and
- There are at least two units, or the first unit spans multiple lines; and
- There is no bare `//` separator between `{` and the first unit; and
- There is no real comment there either.

### Violations (BAD)

```rust
if condition {
    statement_1();

    statement_2();
}
```

A single multiline unit also requires a separator, such as a multiline `match` expression inside a block.

### Compliant (GOOD)

```rust
if condition {
    //
    statement_1();

    statement_2();
}
```

A real comment also satisfies the separation requirement:

```rust
if condition {
    // Set up the initial state
    do_first();
    do_second();
}
```

### Exemptions

- `{` on its own line (`if condition\n{`) needs no separator.
- A compact block containing a single single-line statement needs no separator.
- All enum/struct declarations forbid leading separators.
- Struct literals and field lists inside unions or enum struct variants do not require separators.

### --fix

Insert a `//` line. Two forms: (a) when the first unit is on a later line, insert `//` immediately after `{`, indented like the first unit;
(b) for an inline block `{ first; second; }`, split the line, insert `//`, and reindent the first unit.

## BLK001 - Missing blank line between units

> Message: `missing blank line before this {kind}; previous {kind} ended at line {line_number}`
> `kind`: `statement`, `match arm`, or `enum variant`.

### Trigger conditions

Two consecutive direct units in a `block`, `match_block`, or `enum_variant_list`:

- Are on different lines (`previous.end_point.row != current.start_point.row`); and
- Have no entirely blank line between the previous unit's end and the current unit's start.

### Violations (BAD)

```rust
enum Payload {
    /// First payload.
    First,
    /// Second payload.
    Second,
}
```

### Compliant (GOOD)

```rust
enum Payload {
    /// First payload.
    First,

    /// Second payload.
    Second,
}
```

The same rule applies between statements:

```rust
fn example() {
    //
    let router = router();

    #[cfg(feature = "swagger-ui")]
    let router = router.merge(swagger());

    router
}
```

### Exemptions

- Same-line statements separated by `;` are not checked.
- Struct fields are not checked for blank lines.

### --fix

Insert a blank line before the current unit. If explanatory comments separate two statements, insert the blank line **before the comments** so they stay attached to the statement they describe.

## BLK002 - Redundant bare `//` separator

### Variant 1: Leading separator in declarations and field lists

> Message: `bare // separator is forbidden in this field list; fields here need no separator`

A struct/enum declaration, a struct literal's `field_initializer_list`, or a union/enum struct variant's
`field_declaration_list` has a bare `//` between `{` and its first member. This is forbidden regardless of the member count, multiline members,
or whether `{` occupies its own line. For declarations, the message is
`bare // separator is forbidden at the start of this struct or enum declaration`.

```rust
// BAD
let payload = Payload {
    //
    first: String::new(),
    second: String::new(),
};

// GOOD
let payload = Payload {
    first: String::new(),
    second: String::new(),
};
```

This variant does not flag bare `//` in the enclosing function block (`block`); it only removes separators in field lists.

### Variant 2: Redundant separator in a single-statement block

> Message: `bare // block-start separator is redundant in a single-statement block`

A `block` / `match_block` contains exactly one single-line unit and has a bare `//` between `{` and that unit.

```rust
// BAD
if condition {
    //
    return;
}

// GOOD
if condition {
    return;
}
```

### --fix

Delete the bare `//` line. If only blank lines and bare `//` separate `{` from the unit, delete all of them; if real comments or other content exist, delete only the bare `//` lines.

## BLK003 - Missing blank line between items

> Message: `missing blank line before this {kind}; previous {kind} ended at line {line_number}`
> `kind`: `struct`, `impl block`, `trait`, `function`, `module`, `use declaration`, `constant`, `static`, `type alias`, `enum`, `union`, `macro definition`, `macro invocation`, `extern crate`, `extern block`, etc.

### Trigger conditions

Two consecutive direct items in module scope (top-level `source_file` or an inline `mod { ... }` declaration_list)
have no blank line between them. Items include struct/enum/union/impl/trait/fn/mod/use/const/static/type/macro_rules!/macro invocations/extern crate/extern blocks.

### Violations (BAD)

```rust
use crate::result::BaseRest;
/// Constraints bound into a presigned image upload request.
pub struct ImageUploadSpec<'a> {
    pub object_key: &'a str,
}
```

### Compliant (GOOD)

```rust
use crate::result::BaseRest;

/// Constraints bound into a presigned image upload request.
pub struct ImageUploadSpec<'a> {
    pub object_key: &'a str,
}
```

### Exemptions

- The first item in a container has no preceding item to compare.
- Two same-line items (`fn a() {} fn b() {}`) are not checked.
- Consecutive header items of the same kind (`use` with `use`, `mod` with `mod`) do not require blank lines; use-style manages their internal grouping.
- Methods inside impl/trait/extern bodies are not checked; this rule applies only at module scope.

### Anchor

Leading comments and attributes (`///`, `//`, `#[...]`) move with the item: the blank line belongs before the entire group. When missing,
insert it before the comment/attribute line so comments remain attached to their item.

### --fix

Insert one blank line at the start of the current item's line, or before its leading comments/attributes.

## PARSE001 - Rust syntax parse error (soft warning)

> Message: `Rust syntax tree contains {node.type!r}; spacing results near this location may be incomplete`

Report tree-sitter `ERROR` nodes or `is_missing` nodes. **No fix**: files with parse errors are skipped entirely during fixing to avoid editing based on incomplete node ranges.

## Macro body checks

Tree-sitter parses macro bodies as opaque `token_tree` nodes without `block` / `match_block`
children, so checking only ordinary containers would miss spacing issues in macros such as `tokio::select!`.

The checker **descends into macro bodies** using a two-tier strategy modeled on rustfmt:

1. **Tier 1 (parse as Rust)**: reparse the bytes inside the macro braces independently. If there are **no `ERROR` nodes**,
   apply BLK000/BLK001/BLK002 to arm bodies, nested matches, else blocks, return/break statements, etc.,
   including **statement-level** blank-line checks, not just match arms. Map positions back to the original file using byte offsets.
2. **Tier 2 (`=>` match-like heuristic)**: when reparsing fails on custom syntax (such as select!'s
   `pattern = expr, if guard => body` or a bare match-arm list), split token-level arms at top-level `=>`
   and report arm-level BLK000/BLK001. Split on `=>`, not commas, because an `if guard` comma belongs inside an arm.

All macro-body diagnostics are **warnings** (they do not fail the exit status), with a `(macro body)` message suffix,
and are **never fixed automatically**: custom macro syntax cannot be safely rewritten and needs manual edits.

Skip `macro_rules!` definition bodies (`macro_definition`) and macros delimited by `(...)` / `[...]`
(such as `vec!`, `format!`, and `println!`).

```rust
// BAD - All inside a macro body; reports warnings.
tokio::select! {
    command = recv.recv() => {
        handle();
    }
    task = recv2.recv() => {
        handle2();
    }
}

// GOOD
tokio::select! {
    //
    command = recv.recv() => {
        handle();
    }

    task = recv2.recv() => {
        handle2();
    }
}
```

## Architecture notes

- Outer attributes (`#[...]`) and their following statement form **one unit**, not separate units. `#[cfg(...)]\nlet x = ...` will not falsely trigger BLK001.
- Both checkers use `production_source()` to mask `#[cfg(test)]` modules and `tests/` directories, making test code invisible to spacing checks.

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `ignore_dirs` | Directory names skipped during `.rs` discovery; a match in any path segment excludes the file | `[".git", ".hg", ".svn", ".idea", ".vscode", "target", "node_modules"]` |
