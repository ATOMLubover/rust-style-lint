# trait-use-anonymous

> Trait imports used only for method resolution must use `as _` (anonymous imports).
> Code: `TRAIT001` | `--fix`: unsupported (check only)

## Goal

If a trait is imported with `use` but its name is never explicitly referenced in the file, and the compiler only resolves it through method calls
(`value.method()`), the import exists solely for method resolution. Make it anonymous with
`as _`: the name is not needed in scope, only the trait's methods.

This rule assumes the target code already passes `cargo fmt` and `cargo clippy -D warnings`. Clippy has eliminated
unused ordinary imports, so the linter need not parse dependency source to prove that an import is a trait.

## Trigger conditions (all required)

`TRAIT001` triggers only when **all** of the following hold:

1. The `use` declaration is **private** (no `pub` / `pub(...)`). `pub use` is never checked.
2. The import leaf does not end in `::self`.
3. The import does **not** already use `as _`.
4. The imported local name follows Rust type naming (an uppercase first letter after removing leading `_` characters). Under
   `cargo clippy -D warnings`, trait names must follow this convention; lowercase functions and modules should not be classified as traits.
5. The imported local identifier **never appears** in the file after excluding all `use` declarations. Any of the following counts as
   an explicit use and prevents the diagnostic:
   - The file contains an actual macro invocation matching `macro_markers`; macro expansion may consume its imports, exempting the entire file.
   - A `macro_invocation` contains a whole-word match for the name.
   - An `identifier` / `type_identifier` outside all `use` declarations exactly matches the name.
     This covers trait bounds (`<T: NamedTrait>`), `impl NamedTrait for X`, `dyn NamedTrait`,
     qualified calls (`NamedTrait::method()`), and similar explicit uses.

## Violations (BAD)

```rust
mod traits;
use crate::traits::MethodTrait;          // TRAIT001
use crate::traits::NamedTrait;           // OK - Explicitly used below
use poprako_util::time::ToUnixMilli;     // TRAIT001
struct Value;
impl NamedTrait for Value { fn named(&self) {} }
fn call(value: &Value) { value.ping(); }
fn millis(value: &Value) { value.to_unix_milli(); }
fn bound<T: NamedTrait>() {}
```

- `MethodTrait` is used only through `value.ping()` method resolution; its name never appears, so it is reported.
- `ToUnixMilli` is used only through `value.to_unix_milli()`; its name never appears, so it is reported.
- `NamedTrait` appears explicitly in `impl NamedTrait for Value` and `fn bound<T: NamedTrait>`, so it is not reported.

> Message: `trait import `{path}` is only used for method resolution; import it as `_``

## Compliant (GOOD)

```rust
mod traits;
use crate::traits::MethodTrait as _;
use crate::traits::NamedTrait;
use poprako_util::time::ToUnixMilli as _;
struct Value;
impl NamedTrait for Value { fn named(&self) {} }
fn call(value: &Value) { value.ping(); }
fn millis(value: &Value) { value.to_unix_milli(); }
fn bound<T: NamedTrait>() {}
```

## Handling aliases (`use Foo as Bar`)

`use Foo as Bar` is checked like `use Foo`: `Bar` is the local name used for explicit-reference detection.
If `Bar` is never explicitly referenced after import, `TRAIT001` recommends changing it to `as _`.

## Configuration

Keys come from the `[trait-use-anonymous]` section:

| Key | Description | Default |
| --- | --- | --- |
| `macro_markers` | Macro invocation markers; a matching `macro_invocation` means macro expansion may consume the imports | `[]` |

> `macro_markers` provides a file-level exemption. It only matches actual macro invocations parsed by tree-sitter; identical text in comments, strings, or
> `macro_rules!` definitions does not trigger it.
