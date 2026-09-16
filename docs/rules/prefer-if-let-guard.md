# prefer-if-let-guard

> Rewrite a match with a single business branch as `if`, `if let`, or `let ... else`.
> Code: `LET001` | `--fix`: unsupported (check only; rewrite manually)

## Goal

A match should express multiple business branches. When exactly one arm does actual work and all other arms only exit
(`return`, `break`, `continue`, diverging macros, or empty bodies), the match represents a single execution path
that is clearer as a guard.

```rust
match value {
    Some(x) => foo(x),
    None => return,
}
```

Rewrite as:

```rust
let Some(x) = value else {
    return;
};

foo(x);
```

When there are exactly two arms, one diverging and one empty, use `if let` as well:

```rust
match value {
    Some(x) => {
        return foo(x);
    }
    None => {}
}

if let Some(x) = value {
    return foo(x);
}
```

When all guard bodies are empty, use `if let`:

```rust
match value {
    Some(x) => foo(x),
    None => {}
}
```

Rewrite as:

```rust
if let Some(x) = value {
    foo(x);
}
```

For a two-arm match on boolean literals, use a plain condition rather than `if let true/false`:

```rust
match created {
    true => {},
    false => update(),
}

if !created {
    update();
}
```

## LET001 - Match with a single business arm

> Message (diverging guard): `match has a single business arm `{pattern}` and a diverging guard; prefer `let {pattern} = {scrutinee} else {{ ... }}` over a match whose only other arms bail out`
> Message (empty guards): `match has a single business arm `{pattern}` and only empty guard arms; prefer `if let {pattern} = {scrutinee} {{ ... }}``
> Message (diverging + empty arms): `match has a diverging arm `{pattern}` and an empty opposite arm; prefer `if let {pattern} = {scrutinee} {{ ... }}``
> Message (boolean branches): `boolean match has a single business arm `{pattern}` and an empty opposite arm; prefer `if {condition} {{ ... }}``
> Example: `match has a single business arm `Some(x)` and a diverging guard; prefer `let Some(x) = value else { ... }` over a match whose only other arms bail out`

### Arm classification

Each match arm belongs to one of four categories:

| Category | Condition |
| --- | --- |
| `guarded` | Missing pattern, a pattern with a `condition` (match guard), or missing value |
| `empty` | An empty `block` or `unit_expression` (`()`) |
| `diverging` | The final expression diverges: `return_expression`, `break_expression`, `continue_expression`, or a macro listed in `diverging_macros`. **Only the final expression counts**: a return inside an earlier `if`/`for`/`loop`/`match` does not make the block diverge (`is_diverging_block`), since that branch may not execute. |
| `business` | Anything else (actual work) |

### Trigger conditions (all required)

1. At least two arms, with no `guarded` arms.
2. The match has one of these forms:
   - Exactly one `business` arm and at least one guard arm.
   - Exactly two arms, one `empty` and one `diverging`.
3. For the first form, no guard body refers to bindings introduced by its pattern (`guard_uses_pattern_binding`).
   A `let ... else` block cannot name the failed-match value: `Err(err) => return f(err)` has no clean let-else form and is skipped.
   Detection treats lowercase-leading pattern `identifier` nodes as bindings (uppercase variants such as `None`/`Err` are not bindings).
   If the body references any such binding, skip the match.
4. The pattern of the arm to convert is not a bare `_`.
5. Choose the rewrite based on guard classification:
   - All guards are `empty`, with exactly `true` / `false` arms -> `if condition`; negate when the business arm is `false`.
   - Other cases with all guards `empty` -> `if-let`.
   - All guards are `diverging` **and** their body texts are identical -> `let-else` (merge into one else block).
   - Exactly two arms mixing `empty` + `diverging` -> `if-let` targeting the diverging arm.
   - More than two arms mixing `empty` + `diverging` -> no diagnostic.
   - Different diverging bodies (such as `return` versus `panic!`) -> no diagnostic.

### Violations (BAD)

```rust
// Suggested let-else
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => return,            // Diverging guard
    }
}

// Suggested if-let
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => {},                // Empty guard
    }
}

// A unit-expression guard also counts as empty.
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        _ => (),
    }
}

// A block with a diverging tail, even with preceding statements
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => {
            warn("missing");
            return;
        },
    }
}

// Diverging macro guard (defaults include panic/unreachable/todo/unimplemented)
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => unreachable!(),
    }
}

// Diverging + empty arms -> if-let
pub fn f(member_info: Option<MemberInfo>) {
    match member_info {
        Some(member_info) => {
            return accept(UnitListAccessInfo::Member(member_info));
        }
        None => {}
    }
}

// Multiple guards with identical bodies -> merge into one else block
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => return,
        _ => return,
    }
}

// Guard only binds `_` and references no binding -> rewritable
pub fn f(value: Result<u8, String>) -> u8 {
    match value {
        Ok(x) => x,
        Err(_) => return 0,
    }
}
```

### Compliant (GOOD) - Cases that do not trigger

```rust
// Two business arms
match value {
    Some(x) => foo(x),
    None => bar(),
}

// Dispatch over multiple patterns
match value {
    Some(x) => a(x),
    Some(y) => b(y),
    None => return,
}

// Wildcard business fallback (business pattern is _)
match value {
    Some(x) => foo(x),
    _ => default(),
}

// A match guard cannot be expressed with if-let.
match value {
    Some(x) if x > 5 => foo(x),
    None => return,
}

// The guard body references a binding from its own pattern.
match value {
    Ok(x) => x,
    Err(err) => return err.len() as u8,
}

// More than two arms mixing empty and diverging guards
match value {
    Some(x) => foo(x),
    None => {},
    _ => return,
}

// Guards diverge differently.
match value {
    Some(x) => foo(x),
    None => return,
    _ => panic!("impossible"),
}

// The guard block's tail does not diverge; a return inside an earlier if does not count.
match value {
    Some(x) => foo(x),
    None => {
        if x {
            return;
        }
    },
}

// Single-arm match
match value {
    Some(x) => foo(x),
}
```

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `diverging_macros` | Names treated as diverging macros, without `::` prefixes; compare the last `macro_name` segment | `["panic", "unreachable", "todo", "unimplemented"]` |
| `exclude_segments` | Skip the file if any path segment matches | `[]` |
| `exclude_filename_prefixes` | Skip if any path segment starts with a listed prefix | `[]` |
| `exclude_filenames` | Skip exact filename matches | `[]` |
