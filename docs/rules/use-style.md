# use-style

> Enforce module-level Rust `use` conventions: grouping, merging, ordering, deduplication, `as _`, and structure.
> Codes: `USE_*` family (14 subrules) | `--fix`: supported for most rules

## Goal

`use` declarations must follow a complete set of conventions: leaf-only brace trees, category grouping and ordering, shared-prefix merging,
deduplication, trait imports with `as _`, and structural order and spacing for `use`/`pub use`/`mod`/`pub mod`/`mod tests`.

## Categories and fixed order

Classify each `use` leaf by its root segment:

| Root segment | Category |
| --- | --- |
| `super` | `super` |
| `std` | `std` |
| `crate` or `self` | `crate` |
| Workspace crate name (`workspace_crates`) | `local_crate` |
| Anything else | `third_party` |

Emit groups in this fixed order: `super` -> `std` -> `third_party` -> `local_crate` -> `crate`.
Within each group, sort by kind: `self`(0) -> `name`(1) -> `glob`(2), then by leaf name, then alias.

---

## Rules

### USE_BRACE_NON_LEAF - Brace items must be leaves

> Message: `brace items must be direct leaves, not nested paths`

Every child inside the braces of `use foo::{...}` must be a direct leaf: `identifier`, `crate`, `self`, `super`,
a bare `*`, or `use_as_clause` targeting `identifier` or `self`. Nested paths (`scoped_identifier` or inner `use_list`) violate the rule.

```rust
// BAD
use std::{io::{Read, Write}};

// GOOD
use std::{io, mem};
```

**Fixable**: flatten by rewriting the entire block; skip fixing if parse errors exist.

### USE_SUPER_OUTSIDE_TESTS - `super` imports are allowed only in mod tests

> Message: `` `super` imports are only allowed inside mod tests ``

A `use` starts with `super` outside a lexical `mod tests` block. A `tests.rs` filename or a `tests` path segment also counts as test scope.

```rust
// BAD - Module scope
use super::Something;

// GOOD
#[cfg(test)]
mod tests {
    use super::*;
}
```

**Not fixable**: report only.

### USE_VISIBILITY_BLANK_LINE - Exactly one blank line between ordinary use and pub use

> Message: `ordinary use and pub use blocks need exactly one blank line`

Two consecutive `use_declaration` nodes in the same scope differ in public visibility, with a blank-line count other than one between them.

```rust
// BAD
use std::io;
pub use crate::api;

// GOOD
use std::io;

pub use crate::api;
```

**Fixable**: the structural fixer rewrites the entire block, joining blocks with `\n\n`.

### USE_ITEM_ORDER - Item order

> Message: `private mod, pub mod, mod tests, ordinary use, pub use, then other code`

Semantic children in each scope must appear in ascending rank: private `mod`=0, `pub mod`=1, `mod tests`=2,
ordinary `use`=3, `pub use`=4, other=5. A node whose rank is below the highest preceding rank is a violation.

```rust
// BAD
#[cfg(test)]
mod tests {
    use super::*;
}
mod before;
use std::time::Duration;
pub use crate::api::Api;
fn main_code() {}

// GOOD
mod before;

#[cfg(test)]
mod tests {
    use super::*;
}

use std::time::Duration;

pub use crate::api::Api;

fn main_code() {}
```

**Fixable**: the structural fixer reorders all declarations.

### USE_MOD_GROUP_BLANK_LINE - Module grouping (three messages, one code)

> Message (same-group adjacency): `mod declarations in the same block must be adjacent`
> Message (cfg block separation): `mod blocks with different cfg conditions must be separated by exactly one blank line`
> Message (block separation): `mod blocks must be separated by exactly one blank line`

Within one visibility class (private / pub / `mod tests`), consecutive `mod_item` nodes with the same cfg condition must have no blank lines between them.
Different cfg conditions form separate internal blocks, separated by exactly one blank line. Different visibility blocks also require exactly one blank line.

```rust
// BAD - cfg(all(...)) and cfg(...) form distinct internal blocks.
#[cfg(all(feature = "a", feature = "b"))]
mod both;
#[cfg(feature = "a")]
mod a;

// GOOD
#[cfg(all(feature = "a", feature = "b"))]
mod both;

#[cfg(feature = "a")]
mod a;
#[cfg(feature = "a")]
mod a_extra;
```

```rust
// BAD - Blank line within the same group
/// First module.
mod first;

/// Second module.
mod second;

#[cfg(test)]
mod tests {
    use super::*;
}

// GOOD
mod first;
/// Second module.
mod second;

#[cfg(test)]
mod tests {
    use super::*;
}
```

```rust
// BAD - No blank line between blocks
/// Public module B.
pub mod public_b;
/// Private module A.
mod private_a;
#[cfg(test)]
mod tests {
    use super::*;
}
/// Public module A.
pub mod public_a;

// GOOD
mod private_a;

/// Public module B.
pub mod public_b;

/// Public module A.
pub mod public_a;

#[cfg(test)]
mod tests {
    use super::*;
}
```

**Fixable**: remove blank lines within groups sharing cfg, or add one blank line between different cfg or visibility blocks.

### USE_MULTIPLE_TEST_MODS - At most one `#[cfg(test)]` mod per file

> Message: `only one #[cfg(test)] mod declaration is allowed per file; merge all tests into mod tests`

A file contains more than one `mod_item` with `#[cfg(test)]`; report the second and each later occurrence.

```rust
// BAD
#[cfg(test)]
mod tests_a { }

#[cfg(test)]
mod tests_b { }

// GOOD
#[cfg(test)]
mod tests {
    // all tests here
}
```

**Not fixable**: report only.

### USE_TRAIT_ALIAS_MISSING - Import traits with `as _`

> Message: `` trait import `{full_path}` should use `as _` `` (for example, `` trait import `anyhow::Context` should use `as _` ``).

A leaf with `kind == "name"` has its `full_path` in the configured `traits` list, is not aliased to `_`, and its name is not
explicitly referenced outside `use` declarations (`impl ... Name ... for`, `dyn Name`, `<... as Name>`, `where ...: Name`,
bound positions after `:`, `+`, `<`, or `,`, or inside `derive(...)`). Use declarations are masked with spaces during this check.

```rust
// BAD
use anyhow::Context;

// GOOD
use anyhow::Context as _;
```

**Fixable**: change the alias to `_` and render again.

### USE_MIXED_GROUP - One use tree must not mix categories

> Message: `one use tree must not mix import groups`

The leaves of one `use` declaration belong to multiple categories.

```rust
// BAD
use std::io, crate::thing;

// GOOD
use std::{io, mem};
use crate::thing;
```

**Fixable**: group strictly by category when rendering again.

### USE_GROUP_ORDER - Group order

> Message: `use group appears after a later group`

Within a segment (same cfg condition and attribute signature), consecutive use declarations must follow nondecreasing `CATEGORIES` order.
A statement whose highest category index is below the highest previously seen index violates the rule.

```rust
// BAD - crate(4) precedes std(1).
use crate::local;
use std::io;

// GOOD
use std::io;
use some_crate::Thing;
use crate::local;
```

**Fixable**: reorder by `CATEGORIES` when rendering again.

### USE_GROUP_BLANK_LINE - Adjacent within groups, one blank line between groups

> Message: `different use groups need exactly one blank line`
> Message: `use declarations in the same group must be adjacent`

Two consecutive use declarations in the same segment have different nonempty category sets, and the byte gap between them contains a newline count other than two (not exactly one blank line).
Consecutive declarations with identical category sets must be adjacent, without blank lines; this also applies to uses with matching cfg conditions and attributes.

```rust
// BAD
use std::io::Write;
use crate::local::Thing;

// GOOD
use std::io::Write;

use crate::local::Thing;
```

```rust
// BAD - Same cfg and group, separated by a blank line
#[cfg(feature = "rdb_impl")]
pub use first_crate::First;

#[cfg(feature = "rdb_impl")]
pub use second_crate::Second;

// GOOD
#[cfg(feature = "rdb_impl")]
pub use first_crate::First;
#[cfg(feature = "rdb_impl")]
pub use second_crate::Second;
```

**Fixable**: render with `\n` within groups and `\n\n` between categories.

### USE_CFG_BLOCK_BLANK_LINE - Exactly one blank line between use blocks with different cfg

> Message: `use blocks with different cfg conditions must be separated by exactly one blank line`

Within one ordinary-use or pub-use region, adjacent declarations with different normalized cfg conditions require exactly one blank line between them.
This only partitions internal `use` blocks; it does not change existing ordering or spacing rules between modules, uses, and other items.

```rust
// BAD
#[cfg(all(feature = "a", feature = "b"))]
use std::cmp::Ordering;
#[cfg(feature = "a")]
use std::mem::take;

// GOOD
#[cfg(all(feature = "a", feature = "b"))]
use std::cmp::Ordering;

#[cfg(feature = "a")]
use std::mem::take;
#[cfg(feature = "a")]
use std::time::Duration;
```

**Fixable**: the structural fixer inserts `\n\n` between cfg blocks.

### USE_DUPLICATE_IMPORT - Deduplicate imports

> Message: `duplicate imports must be merged`

After aliasing, a leaf tuple `(prefix, leaf, kind, alias)` occurs more than once.

```rust
// BAD
#[cfg(feature = "a")]
use std::{mem::take, time};
#[cfg(feature = "a")]
use std::mem::take;

// GOOD - After merging
#[cfg(feature = "a")]
use std::{mem::take, time};
```

**Fixable**: deduplicate by identity and render again.

### USE_MISSING_MERGE - Shared prefixes must merge into one brace tree

> Message: `` imports under `{prefix}` must share one use tree `` (for example, `` imports under `std::io` must share one use tree ``).

Two or more leaves in the same segment share a `prefix`, but no single source use declaration contains exactly that group of leaves.

```rust
// BAD
use std::io::Read;
use std::io::Write;

// GOOD
use std::io::{Read, Write};
```

**Fixable**: bucket by prefix and render each bucket as one brace tree.

### USE_PARSE_ERROR - Unparseable use declaration

> Three messages: `invalid scoped use list` / `invalid use alias` / `unsupported use node {type}`.

A `scoped_use_list` is missing `path` or `list`; a `use_as_clause` has a named-child count other than two; or a use-tree node's type is outside
`{identifier, crate, self, super, scoped_identifier, use_wildcard, use_as_clause, use_list, scoped_use_list}`.

**Not fixable**: a parse error skips rewriting the entire segment (`render_block` does not run when `USE_PARSE_ERROR` exists).

---

## Rule priority and architecture notes

- Item order, grouping, and test-module structure are analyzed on **original source** (`production_source()` masks `#[cfg(test)]` declarations,
  which would hide declarations that need ordering). Other `use` analysis runs on masked production source.
- The structural fixer owns all structural edits: collect all scoped `mod` + `use` declarations, render a canonical header (modules first, then uses),
  rebuild it at the start of the scope, and remove original declaration locations. Before rebuilding, verify every declaration appears in the header; refuse the edit if any are missing (never drop declarations).
  `check_mod_grouping` only reports; the structural fixer's single reconstruction also resolves spacing issues.

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `traits` | Full paths. Matching trait imports require `as _` unless the name is explicitly referenced | `anyhow::Context`, `futures::FutureExt`, `futures::StreamExt`, `itertools::Itertools`, `serde::Deserialize`, `serde::Serialize`, `std::convert::TryFrom`, `std::convert::TryInto`, `std::io::BufRead`, `std::io::Read`, `std::io::Write`, `std::iter::Iterator`, `tokio_stream::StreamExt`, `tracing::Instrument` |

The `traits` table replaces the entire `[use-style]` section in `defaults.toml`; it is not incremental.
