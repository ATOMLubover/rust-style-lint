# module-dependency

> Enforce downward-or-across Rust module dependencies; reject upward dependencies and cycles.
> Codes: `MOD001` (upward dependency), `MOD002` (cyclic dependency) | `--fix`: unsupported (check only)

## Goal

The module hierarchy is a tree rooted at the crate root. A reference edge (one module using another) may point **downward** to a deeper descendant
or **across** to a sibling or lower-level module, but must not point **strictly upward** to an ancestor. A lower module must not depend on its ancestors.

For `parent` and `parent::child`:

- `parent` uses something from `parent::child` -> allowed (downward).
- `parent::child::a` uses something from `parent::child::b` -> allowed (across).
- `parent::child` uses something from `parent` -> **forbidden**, reports `MOD001`.

## MOD001 - Upward dependency

> Message: `{source} must not depend only upward on strict ancestor {target}; reference: `{reference}``

Triggers when module A depends on module B and B is A's **strict ancestor** in the module tree (`len(B) < len(A)` and `A[:len(B)] == B`).
All three edge sources are checked:

1. `use` declarations.
2. Qualified paths outside `use` declarations (`scoped_identifier` / `scoped_type_identifier`).
3. Paths embedded in outer attribute arguments (such as `#[allow(crate::parent::lint)]`; the regex only matches `crate::`, `self::`, or `super::` prefixes).

### Violations (BAD)

`src/parent/child.rs` - Child depending upward on its parent (six occurrences):

```rust
use super::Owner as ParentOwner;
pub use super::Owner;
use super::*;
fn alias(_: ParentOwner) {}
fn qualified(_: crate::parent::Owner) {}
#[allow(crate::parent::lint)] struct Attributed;
```

`src/parent/impls.rs` - Another upward dependency:

```rust
use super::Owner;
use crate::port::Trait;
impl Trait for Owner {
    fn use_again(&self) { let _: Option<Owner> = None; }
}
```

- `Owner` in the `impl Trait for Owner` header is exempt (see exemption 2 below), but
  `Option<Owner>` in the `use_again` function body is the seventh upward dependency.

### Compliant (GOOD)

`src/parent/child.rs` - Child depending on sibling `parent::shared`:

```rust
use super::shared::Helper;
pub struct Child;
fn helper(_: Helper) {}
```

`src/parent.rs`:

```rust
pub struct Owner;
pub mod child;
pub mod shared;
pub mod impls;
use self::child::Child;
fn child(_: Child) {}
```

`src/lib.rs`:

```rust
mod parent;
mod port;
mod part_impl;
#[cfg(test)]
mod tests;
```

## MOD002 - Cyclic dependency

> Message: `cyclic module dependency {source} -> {target} in [{cycle members}]`

Build a graph from all collected dependency edges and detect strongly connected components with Tarjan's algorithm. Every SCC containing more than one module is a cycle.
Each edge inside the cycle (both source and target belong to that SCC) reports one `MOD002`.

### Violations (BAD)

```rust
// src/lib.rs
mod a;
mod b;
mod c;

// src/a.rs
use crate::b::B;
pub struct A(B);

// src/b.rs
use crate::c::C;
pub struct B(C);

// src/c.rs
use crate::a::A;
pub struct C(A);
```

`a -> b -> c -> a` contains three edges and reports three `MOD002` diagnostics.

## MOD001 vs MOD002

| | MOD001 | MOD002 |
| --- | --- | --- |
| Property | Single upward dependency | Cycle participation |
| Trigger | The edge points to a strict ancestor, whether or not a larger cycle exists | The edge is within a multi-module SCC |
| Relationship | - | An edge that is both upward and cyclic reports both MOD001 and MOD002 |

## MOD003 - Duplicate definitions (warning, does not fail)

> Message: `duplicate {type alias|struct|trait} name `{name}` defined in {modules}`

Scan all production modules and collect module-level `type` aliases, structs, and traits. When the same name is defined in **two or more distinct modules**,
report `MOD003` at each definition. For example, a parent module `engine` defining `pub type AgentEngineResult`
and its child `engine::lifecycle` privately defining `type AgentEngineResult` to shadow it produce two `MOD003` warnings.

**`MOD003` uses `level="warning"` and does not fail the run**: the runner returns nonzero only for error-level violations.
It flags naming collisions and shadowing as undesirable but does not block otherwise compilable code.

Definitions in test modules (matching `prefixes`) are not counted.

## Path resolution (super / self / crate)

- `crate::` or the crate name (read from `Cargo.toml`) -> resolve relative to crate root `()`.
- `self::` -> relative to the current module.
- `super::` -> relative to the parent; `super::super::` climbs one level per segment.
- A top-level module name (first segment matches a known root module) -> treat as prefixed with `crate::`.
- Unrecognized paths (external crate names / unknown identifiers) -> produce no edge and are ignored.

When expanding a `use` tree, fold `self` into the current prefix, expand `*`, and track `as` aliases for pure-impl-header filtering.

## Computing module paths

**File path -> module tuple** (`file_module`):

| File | Module path |
| --- | --- |
| `src/lib.rs` / `src/main.rs` (top level) | `()` (crate root) |
| `src/foo.rs` | `("foo",)` |
| `src/foo/bar.rs` | `("foo", "bar")` |
| `src/foo/mod.rs` | `("foo",)` (remove `mod.rs`) |

**Inline modules**: walk upward from the AST node, prepending the name of each `mod_item` with a `body`.

## Exemptions (edges that do not report violations)

1. **`#[cfg(test)]` code**: items guarded by `cfg(test)` or an equivalent condition contribute no edges. `CfgParser` fixes `test` to `False`; an item is test-only only if every assignment is `False`.
2. **Pure impl-header references**: qualified paths in the trait / self-type positions of `impl_item` (`impl Trait for MyType`) are exempt. This does **not** directly exempt `use` edges: `alias_is_pure_impl()` separately checks whether every use of the imported alias is in an impl header, exempting the entire use edge if so.
3. **Self references**: skip edges where `target == source`.
4. **Unresolved paths**: no edge is produced when `absolute_path()` or `target_module()` returns `None`.

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `exclude_files` | Paths relative to root; matching files are skipped entirely (no edges or violations) | `[]` |

There is no `[module-dependency]` section in `defaults.toml`; the project's `exclude_files` list is complete.
