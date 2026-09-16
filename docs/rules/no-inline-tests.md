# no-inline-tests

> Forbid inline `#[cfg(test)] mod tests { ... }`: tests must live in a separate `tests.rs` file.
> Code: `TST001` | `--fix`: unsupported (check only)

## Goal

Test modules must use **file-based declarations**:

```rust
#[cfg(test)]
mod tests;
```

The declaration ends in a semicolon and has no `body`. Put tests in a separate `src/tests.rs` (or `src/<parent>/tests.rs` for a submodule)
or `src/tests/mod.rs`. Any inline `#[cfg(test)] mod tests { ... }` block is a violation.

## Trigger conditions (all required)

1. The AST node is a `mod_item`.
2. Its `name` field is **exactly** `"tests"`. Other names are not checked.
3. The module has a `body`: an inline brace block, **not** a semicolon declaration.
4. `has_test_only_cfg` returns true: the module or an ancestor has `#[cfg(test)]` or an equivalent attribute.
   `CfgParser` fixes `test` to `False`; a module is test-only only if every possible assignment evaluates to `False`.
   This handles complex forms such as `#[cfg(all(test, feature = "x"))]`, `#[cfg(any(test))]`, and `#[cfg(not(not(test)))]`.

All nesting levels are checked, not just the top level.

## Violations (BAD)

```rust
// src/things.rs
pub fn do_thing() {}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn it_works() {}
}
```

These two variants also report `TST001`:

```rust
#[cfg(test)]
// This comment belongs to the test module.
mod tests {   // A comment between cfg and mod does not exempt the module.
    use super::*;
}
```

```rust
// Inline tests in src/lib.rs itself
pub mod things;
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn it_works() {}
}
```

> Message: `inline #[cfg(test)] mod tests { ... } is forbidden; extract tests into a separate tests.rs file`

## Compliant (GOOD)

```rust
// src/lib.rs
pub mod things;
#[cfg(test)] mod tests;
```

```rust
// src/things.rs
pub fn do_thing() {}
#[cfg(test)]
// Tests remain in a sibling file.
mod tests;
```

The semicolon declaration `mod tests;` has no body and does not trigger the rule.

## Configuration

This checker **reads no configuration keys**. There is no corresponding section in `defaults.toml`.
