# visibility-style

> Enforce plain Rust visibility (only `pub` and private) and private implementation fields.
> Codes: `VIS001` (restricted visibility), `VIS002` (public fields outside allowlisted modules) | `--fix`: unsupported (check only)

## Goal

Production code permits only two visibility forms:

- **Plain `pub`**: the `pub` keyword without a parenthesized restriction.
- **Private**: no visibility modifier (module-local).

All other forms (`pub(crate)`, `pub(super)`, `pub(self)`, `pub(in path)`) are restricted visibility, forbidden by `VIS001`.
Struct fields that are mutable by default must be private; public fields are allowed only in explicitly allowlisted modules.

## VIS001 - Restricted visibility

> Message: `restricted visibility `{visibility}` is forbidden; production Rust permits only plain `pub` or private items; share an internal item with plain `pub` behind a private module`
> Example: `restricted visibility 'pub(crate)' is forbidden; ...`

### Trigger conditions

Depth-first traversal encounters a `visibility_modifier` node, and:

1. Its module is not excluded. Exclusions cover `mod` declarations with `#[cfg(test)]` (or a condition that `CfgParser` determines is always false when `test=false`),
   or items with an attribute for which `has_test_only_cfg()` is true, checking up the ancestor chain.
2. The normalized visibility modifier text is **not** exactly `"pub"`.

## VIS002 - Public struct fields outside allowlisted modules

> Message: `public struct field is forbidden in {formatted_module}; {rule}; expose construction or access through functions`
> `{formatted_module}`: `crate` for an empty module path; otherwise `crate::` followed by segments joined with `::`.
> `{rule}`: `plain-public struct fields are allowed only in crate::model, crate::data; fields elsewhere must be private` for a nonempty allowlist; `... in no module; ...` for an empty one.

### Trigger conditions

A visibility modifier node is encountered, and:

1. The module is not excluded (same as VIS001).
2. The visibility belongs to a **struct field**: the parent is `field_declaration` and its grandparent's parent is `struct_item` (named fields),
   or the parent is `ordered_field_declaration_list` and the grandparent is `struct_item` (tuple struct fields).
3. The current module is not in `allow_public_fields`: its path does not start with any allowlisted module prefix.

### VIS001 and VIS002 can both trigger

These checks are **independent**. A `pub(crate)` field outside allowlisted modules reports both VIS001 (not plain `pub`) and VIS002 (a field outside the allowlist).

### Violations (BAD) - `src/service.rs`: 6 VIS001 + 4 VIS002

```rust
pub(crate) struct Restricted;            // VIS001
pub(super) fn parent_only() {}           // VIS001
pub(self) const LOCAL: i32 = 1;         // VIS001
pub(in crate::service) type Scoped = i32; // VIS001
pub struct Named { pub value: i32, pub(crate) other: i32 }
//                        VIS002          VIS001 + VIS002
pub struct Tuple(pub i32, pub(super) i32);
//              VIS002   VIS001 + VIS002
#[cfg(test)] pub(crate) fn ignored() {}              // Skipped
#[cfg(all(test, feature = "rdb"))] pub struct TestOnly { pub value: i32 }  // Skipped
```

### Compliant (GOOD)

```rust
// src/model.rs - Allowlisted; public fields permitted
pub struct Model { pub value: i32 }

// src/data.rs - Allowlisted
pub struct Data(pub i32);

// src/service.rs - Not allowlisted: private fields, plain pub or private visibility only
pub struct Service { value: i32 }
pub struct Tuple(i32);
```

## CfgParser and test-only detection

The checker includes a small cfg evaluator: it computes all possible truth values of `#[cfg(...)]` with **`test=false` fixed**.
If the expression can never be true with `test=false`, the item is test-only and all visibility rules are skipped.
`has_test_only_cfg()` walks ancestors; `#[cfg(test)]` on a parent `mod` declaration propagates to every item inside.

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `allow_public_fields` | Module paths allowing public struct fields, separated by `::` and relative to the crate root (such as `"model"`, `"part::repo::oper"`) | `[]` |
| `exclude_files` | File paths relative to root; skip the entire file | None (not defined in defaults.toml) |
