# no-redundant-destructure

Forbids binding an expression to a plain temporary variable and immediately
destructuring that variable in the next statement.

## Violation

Code: `DSTR001`

Message:

```text
temporary `<name>` is immediately destructured; bind `<pattern>` directly to the original expression
```

## BAD

```rust
let page_import_results = import_pages(
    repo,
    context,
    &page_scopes,
).await?;

let (final_page_counters, imported_page_count, imported_unit_count) =
    page_import_results;
```

## GOOD

```rust
let (final_page_counters, imported_page_count, imported_unit_count) = import_pages(
    repo,
    context,
    &page_scopes,
).await?;
```

Tuple, tuple-struct, struct, and slice destructuring are covered, including
those nested below a reference pattern. The two `let` declarations must be
adjacent. An intervening comment or statement prevents a violation. Refutable
`let ... else` declarations are excluded because their `else` block may still
need the temporary variable.

`--fix` is not supported because preserving expression and pattern formatting
requires more context than a safe mechanical edit.
