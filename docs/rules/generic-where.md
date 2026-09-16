# generic-where

> Generic type and lifetime bounds must appear in `where` clauses; argument-position `impl Trait` is forbidden.
> Do not split bounds for the same subject across predicates in one `where` clause.
> Codes: `GEN001` (inline bounds), `GEN002` (argument-position impl Trait), `GEN003` (repeated where predicate), `GEN004` (call-site bounds in trait definitions) | `--fix`: unsupported (check only)

## Goal

- Move generic parameter bounds (`T: Copy`, `'a: 'static`) into a `where` clause.
- Argument-position `impl Trait` is forbidden: introduce a named generic parameter and put its bounds in `where`.
  Return-position `impl Trait` (`-> impl Trait`) is **allowed**.
- Each subject may appear only once in a `where` clause; merge all of its bounds.
- Trait definitions must not impose `Clone`, `Send`, `Sync`, or `'static`; callers must impose these bounds at the call site.

## GEN001 - Inline generic parameter bounds

> Message: `generic parameter {parameter_name} in {declaration_name} uses an inline bound; move the bound to a where clause`

### Trigger conditions

A declaration of one of the following kinds has a generic parameter (`type_parameter` or `lifetime_parameter`) with a `bounds` field (`: Bound` syntax):
`enum_item`, `function_item`, `impl_item`, `struct_item`, `trait_item`, `type_item`, `union_item`.
Both type parameters (`T: Copy`) and lifetime parameters (`'a: 'static`) are checked.
An `impl` item has no name field, so `{declaration_name}` is `"impl"` in its message.

### Violations (BAD) - Nine occurrences

```rust
fn bad_fn<T: Copy, 'a: 'static>() {}    // Two: T: Copy and 'a: 'static
impl<T: Copy> Item<T> {}                // One occurrence
struct BadStruct<T: Copy> {}            // One occurrence
enum BadEnum<T: Copy> {}                // One occurrence
trait BadTrait<T: Copy> {}              // One occurrence
type BadAlias<T: Copy> = Vec<T>;        // One occurrence
union BadUnion<T: Copy> { value: T }    // One occurrence
```

A `mod maybe_production { fn bad_mod<T: Copy>() {} }` guarded by `#[cfg(any(test, feature = "extra"))]`
is **not exempt**: after fixing `test` to false, `any(...)` can still be true through `feature = "extra"`, so it counts as production code.

### Compliant (GOOD)

```rust
fn clean<T>() {}
fn constrained<T>() where T: Copy {}
struct Item<T> where T: Copy {}
impl<T> Item<T> where T: Copy {}
fn return_opaque() -> impl Iterator<Item = u8> { todo!() }
#[cfg(test)]
mod tests { fn ignored<T: Copy>() {} }   // Test code is masked and not checked.
```

## GEN002 - Argument-position `impl Trait`

> Message: `inline impl Trait is forbidden; introduce a named generic parameter and move the bound to a where clause`

### Trigger conditions

A tree-sitter `abstract_type` node (`impl Trait` syntax) is **not in return position**.
Return position is determined by walking up the `bounded_type` parent chain and checking whether the node is the enclosing declaration's `return_type` field.
Arguments, `let` bindings, type aliases, and all other non-return contexts trigger the rule.
References wrapping `impl Trait` (`&(impl EffectDevelop + Sync)`) are still detected; nested `bounded_type` chains are traversed.

### Violations (BAD) - Two occurrences

```rust
fn bad_impl_trait(develop: &(impl EffectDevelop + Sync), other: impl Other) {}
//                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^
//                 GEN002 #1                             GEN002 #2
```

### Compliant (GOOD) - Return position is allowed

```rust
fn return_opaque() -> impl Iterator<Item = u8> { todo!() }
```

## GEN003 - Repeated where predicate

> Message: `where predicate for {left} is repeated; merge all bounds for {left} into one predicate`

### Trigger conditions

Visit every `where_clause` and read the `left` field of each `where_predicate`.
Within a clause, the first occurrence of an exact `left` is valid; every subsequent occurrence is reported separately.
The check is independent of the enclosing declaration kind, covering functions, types, traits, impls, and associated items.
Type parameters, lifetimes, associated types, and other complex subjects are handled uniformly; separate `where` clauses do not affect each other.

### Violations (BAD)

```rust
impl<L> Step<L> for Repo
where
    L: Level + Send,
    L: AtLeast<RepeatableRead>, // GEN003
{}
```

### Compliant (GOOD)

```rust
impl<L> Step<L> for Repo
where
    L: Level + Send + AtLeast<RepeatableRead>,
{}

fn distinct<T, U>()
where
    T: Copy,
    U: Send,
{}
```

## GEN004 - Call-site bounds in trait definitions

> Message: `trait {trait_name} constrains {bound}; move this bound to the call site`

`Clone`, `Send`, `Sync`, and `'static` express caller requirements rather than the trait's contract. They must not appear in
supertraits, generic parameters, `where` clauses, associated types, or method declarations within a trait. Each bound
reports a separate `GEN004`. Functions, impls, and other call sites outside traits may use these bounds normally.

### Violations (BAD)

```rust
trait Worker: Clone + Send + Sync + 'static {
    type Item: Clone;

    fn process<T: Send>(&self)
    where
        T: Sync;
}
```

### Compliant (GOOD)

```rust
trait Worker {
    type Item;

    fn process<T>(&self);
}

fn process_worker<T>(worker: &impl Worker)
where
    T: Clone + Send + Sync + 'static,
{
}
```

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `exclude_files` | Paths relative to root; exact matches skip the entire file | `[]` |

There is no `[generic-where]` section in `defaults.toml`, and this checker does not use `merged()`; `config=None` excludes no files.
