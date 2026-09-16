# no-unnamed-fields

> Forbid tuple (unnamed) enum variants; every variant field must be named.
> Code: `ENUM001` | `--fix`: unsupported (check only)

## Goal

**All enum variant fields must be named**. Each field needs a name that expresses its meaning;
positional fields such as `Variant(String, u32)` force readers to guess what the second field represents.

Unit variants (no fields) and struct variants (`{ name: Type }`) are allowed and produce no diagnostic.

```rust
enum Error {
    Model(String, u32),            // BAD - Unnamed fields
    Empty(),                       // BAD - Empty tuples are also forbidden
    Unit,                          // GOOD - No fields
    Model { message: String },     // GOOD - Named fields
    Model { kind: u32 },           // GOOD - Named fields
}
```

## Trigger conditions

An `enum_variant` AST node has an `ordered_field_declaration_list`
in its named_children: the tuple form `Variant(...)`.

- Single-field `Variant(u8)`, multi-field `Variant(String, u32)`, and empty `Variant()` tuples **all trigger** the rule.
- Explicit discriminants such as `Variant = 5` (an `integer_literal` child) **do not trigger** it.
- `Variant { x: u8 }` (`field_declaration_list`) and unit `Variant` **do not trigger** it.

> Message: `enum variant `{name}` has unnamed fields; give every field a name or use a unit variant`
> `{name}` is the variant name (the text of its `name` field).

## Violations (BAD)

```rust
pub enum ModelError {
    Model(String, u32),
}
```

```rust
pub enum ApiError {
    NotFound,
    BadRequest(String),
    Timeout(u64),
}
```

Single-field and empty tuples also report `ENUM001`:

```rust
pub enum Token {
    Value(String),   // BAD - Single fields must also be named
    Empty(),         // BAD - Empty tuples should be unit variants
}
```

## Compliant (GOOD)

```rust
pub enum ModelError {
    Model { message: String, code: u32 },
}
```

```rust
pub enum ApiError {
    NotFound,
    BadRequest { detail: String },
    Timeout { after_ms: u64 },
}
```

```rust
pub enum Token {
    Value { text: String },
    Missing,
}
```

> Naming is a semantic decision (what to call fields and which to keep) that cannot be inferred mechanically, so the checker reports without rewriting.

## Configuration

This checker **reads no configuration keys**. There is no corresponding section in `defaults.toml`.
