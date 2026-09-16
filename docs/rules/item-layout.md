# item-layout

> Enforce declaration and helper function order in handwritten Rust source.
> Codes: `LAYOUT001`-`LAYOUT004` | `--fix`: unsupported (check only)

## Goal

- Impl blocks must immediately follow their struct declaration.
- Inherent impls must precede trait impls.
- Private functions must follow all public functions.
- Private functions must follow first-call order.

The checker recurses into `impl_item` and `mod_item` bodies, checking each nested container independently.

## LAYOUT001 - Impls must immediately follow their struct

> Message: `impl for {struct_name} must immediately follow its struct declaration`

A struct has one or more impl blocks, but another named item separates the struct from its first impl or separates two impls.
All impl blocks must be contiguous and immediately follow the struct.

```rust
// BAD - A const separates the struct from its impl.
pub struct Wrong;
const SEPARATES_WRONG_IMPL: () = ();
impl Default for Wrong { fn default() -> Self { Self } }
impl Wrong { fn create() {} }

// GOOD
pub struct Good;
impl Good { pub fn create() {} fn prepare() {} }
impl Default for Good { fn default() -> Self { Self } }
pub fn run() { prepare(); finish(); }
fn prepare() {}
fn finish() {}
```

## LAYOUT002 - Inherent impls must precede trait impls

> Message: `inherent impl for {struct_name} must precede its trait impls`

A struct has multiple impls, with an inherent impl (no trait field) appearing **after** a trait impl.
All inherent impls must precede all trait impls; after encountering a trait impl in source order, every later inherent impl is a violation.

```rust
// BAD - Trait impl before inherent impl
impl Default for Wrong { fn default() -> Self { Self } }
impl Wrong { fn create() {} }

// GOOD - Inherent impl first
impl Good { pub fn create() {} fn prepare() {} }
impl Default for Good { fn default() -> Self { Self } }
```

## LAYOUT003 - Private functions must follow all public functions

> Message: `private functions must follow all public functions`

Within a container (module, impl body, or any scope containing functions), a private function's byte offset precedes the last public function.
The rule triggers when the maximum public function offset exceeds the minimum private function offset. `#[cfg(test)]` functions are excluded.

```rust
// BAD - Private second precedes public run.
fn second() {}
/// Calls helpers in their required order.
pub fn run() { first(); second(); }
fn first() {}

// GOOD - Public functions first, private functions afterward.
pub fn run() { prepare(); finish(); }
fn prepare() {}
fn finish() {}
```

## LAYOUT004 - Private functions follow first-call order

> Message: `private function {name} must follow first-call order; {earlier_name} is called earlier`

Private functions in a container are not sorted by first-call position. The expected order uses the **byte position of the first call** to each private function from any function in that container,
breaking ties by definition offset. Functions never called use `sys.maxsize` and go last.

```rust
// BAD - second is defined first, but first is called earlier.
pub fn run() { first(); second(); }
fn second() {}
fn first() {}

// GOOD
pub fn run() { first(); second(); }
fn first() {}
fn finish() {}   // Never called -> place last.
```

## One fixture triggering every code (self-test asserts all four)

```rust
pub struct Wrong;
const SEPARATES_WRONG_IMPL: () = ();
impl Default for Wrong { fn default() -> Self { Self } }   // LAYOUT001: separated by const
impl Wrong { fn create() {} }                              // LAYOUT002: inherent after trait
/// Runs the private helpers.
fn second() {}                                             // LAYOUT003: private function order
/// Calls helpers in their required order.
pub fn run() { first(); second(); }
/// Runs before the second helper.
fn first() {}                                              // LAYOUT004: second should be defined before first
```

Expected code set: `{LAYOUT001, LAYOUT002, LAYOUT003, LAYOUT004}`.

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `exclude_files` | Paths relative to root; exact `==` matches skip the entire file | `[]` |

There is no `[item-layout]` section in `defaults.toml`, and the checker does not use `merged()`; `exclude_files` comes entirely from project configuration.
