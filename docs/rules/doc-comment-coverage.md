# doc-comment-coverage

> User-defined identifiers require the appropriate comment style: outer doc comments (`///` / `/**`) for public items and regular comments (`//` / `/*`) for private items.
> Code: `DOC001` | `--fix`: unsupported (check only)

## Goal

Scan all Rust source files under `src/` and report declarations without the required preceding comment:

- **Public items** require an outer doc comment (`///` or `/**`).
- **Private items** require a regular comment (`//` or `/*`).

## Covered declarations (14 AST node kinds)

Module-level items: modules, functions, structs, enums, traits, type aliases, constants, statics, macro definitions, and unions.
Trait members: associated functions, type aliases, and constants (implicitly public).
Enum variants: every variant (implicitly public).
Inherent methods: public functions in impl blocks.

Node kinds: `associated_type`, `const_item`, `enum_item`, `enum_variant`, `field_declaration`,
`function_item`, `function_signature_item`, `macro_definition`, `mod_item`, `static_item`,
`struct_item`, `trait_item`, `type_item`, `union_item`.

## DOC001 - Missing required comment

> Message (assembled dynamically): `{visibility} {declaration type} '{name}' is missing a {comment_kind}`
> Example: `public function item 'undocumented_fn' is missing a doc comment`
> `comment_kind`: `doc comment` for public items; `regular comment` for private items.

The diagnostic points to the insertion location: when attributes precede an item, it reports the first attribute's starting line. Insert the comment above the entire attribute group.
Without attributes, it reports the declaration's starting line rather than the identifier's line. Multiline attributes and comments between attributes do not change this rule.

### Trigger conditions (all required)

1. The file is under `src/` and is not excluded (see configuration).
2. The node is one of the 14 kinds above.
3. The item is not a test (`#[test]`, `#[tokio::test]`, or an attribute containing `rstest`).
4. The item is not the `main` function in `src/main.rs`.
5. The node has a `name` field.
6. The required preceding comment is missing:

   - **Public items**: scan backward, skipping `attribute_item` nodes. The first non-attribute sibling must be a `line_comment` starting with `///`
     or a `block_comment` starting with `/**`.
   - **Private items**: the first non-attribute sibling must be a nonempty `line_comment` starting with `//` (but **not** `///`),
     or a nonempty `block_comment` starting with `/*` (but **not** `/**` or `/*!`).
     A bare `//` with no content is a block separator and does not count as a comment.

### Public versus private

Public: any `visibility_modifier` (`pub`, `pub(crate)`, etc.); an `enum_variant`; a `field_declaration` whose enclosing struct/enum/union
has a visibility modifier; or an item nested under a `trait_item` ancestor.
Private: nested under a `closure_expression` or `function_item` ancestor (local items), without satisfying a public condition.

### Violations (BAD) - Nine public items

```rust
pub fn undocumented_fn() {}

pub struct UndocumentedStruct;

pub trait UndocumentedTrait {
    fn undocumented_trait_method(&self);
}

pub enum UndocumentedEnum {
    FirstVariant,
}

pub type UndocumentedType = u32;

/// This one is documented.
pub fn documented_fn() {}

// This is a regular comment, NOT a doc comment.
pub fn still_undocumented() {}

pub mod undocumented_mod;
```

Using `///` on a private item also violates the rule:

```rust
// BAD - Two violations
fn uncommented_private_fn() {}

/// A doc comment is not a private implementation comment.
struct WronglyDocumentedPrivateStruct;
```

### Compliant (GOOD) - Public items

```rust
/// A documented public function.
pub fn documented_fn() {}

/** A documented public struct. */
pub struct DocumentedStruct;

/// A documented trait.
pub trait DocumentedTrait {
    /// A documented trait method.
    fn trait_method(&self);
}

/// A documented enum.
pub enum DocumentedEnum {
    /// A documented variant.
    First,
}

/// A documented type alias.
pub type DocumentedType = u32;

/// A documented const.
pub const ANSWER: u32 = 42;

/// A documented module.
pub mod documented_mod;
```

Private items use regular comments:

```rust
// A documented implementation detail.
fn private_fn() {}

/* A private implementation type. */
struct PrivateStruct;

// A private module.
mod private_mod;
```

Attributes may appear between the doc comment and the item (`attribute_item` nodes are skipped):

```rust
/// Documented with an attribute in between.
#[derive(Debug)]
pub struct Attributed;
```

### Skipped items

| Condition | Mechanism |
| --- | --- |
| `#[test]` / `#[tokio::test]` / `#[rstest::...]` items | Skipped by `is_test_item()` |
| `main` function in `src/main.rs` | Skipped by `is_main_in_main_rs()` |
| Inner doc comments (`//!`, `/*!`) | Document the enclosing module, not the following item; `/*!` does not count as a private comment |
| Bare `//` with no content | Treated as a block separator |
| Test fixtures / generated files | Excluded by configuration |

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `exclude_segments` | Skip the file if any path segment matches | `["tests", "entity"]` |
| `exclude_filename_prefixes` | Skip if any path segment starts with a listed prefix | `["test_"]` |
| `exclude_filenames` | Skip exact filename matches | `["schema.rs", "entity.rs"]` |
