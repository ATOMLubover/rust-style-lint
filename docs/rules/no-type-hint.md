# no-type-hint

> Forbid type annotations on `let` bindings; use inference or turbofish instead.
> Code: `NO_TYPE_HINT` | `--fix`: **no-op** (reports only, without editing)

## Goal

The rule is uniform: **every** `let x: T = value` is forbidden. When inference is insufficient, pin the type on the value's generic call
with turbofish rather than annotating the binding.

```rust
let x = expr.collect::<Vec<_>>();       // GOOD
let y = resolver.parse::<u32>()?;       // GOOD - Turbofish on the call
let z: u32 = expr.parse()?;             // BAD - Type hint on the let binding
```

## NO_TYPE_HINT - Type annotation on a let binding

> Message: `type hint `{type_ann}` on let binding; remove the annotation and rely on inference, or pin the type with turbofish on the value's generic call`
> `{type_ann}` is the complete annotation node text, such as `u32`, `HashMap<String, i32>`, or `Vec<&str>`.

### Trigger conditions

A `let_declaration` has a nonempty `child_by_field_name("type")`: the binding carries a type annotation.

**No exceptions**: `let x: T = ...` triggers the rule regardless of type complexity, macro expansion, or context.

### Alternatives

| Style | Result | Example |
| --- | --- | --- |
| Type annotation on `let` | BAD -> NO_TYPE_HINT | `let z: u32 = expr.parse()?;` |
| Turbofish on a call | GOOD | `let y = resolver.parse::<u32>()?;` |
| Inferred binding type | GOOD | `let x = expr.collect::<Vec<_>>();` |

## Why --fix is a no-op

`fix_file()` exists but explicitly leaves the source unchanged:

> Removing an annotation can silently change the inferred type (literals, `as` casts,
> generic constructors) or break compilation entirely (Diesel `.first()?` / `.load()`),
> and turbofish is not valid on every method. Auto-editing is therefore a no-op:
> the check reports, and the developer applies the recommended fix by hand.

`--fix` is accepted for API compatibility but prints the following to stderr:

```
note: --fix is a no-op; remove or turbofish the annotation by hand
(auto-editing can change the inferred type or break compilation)
```

## Configuration

**No configuration keys**. There is no `[no-type-hint]` section in `defaults.toml`. Test code is masked with `production_source()` before parsing,
but the checker itself has no configurable behavior.
