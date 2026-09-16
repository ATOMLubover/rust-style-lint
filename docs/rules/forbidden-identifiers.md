# forbidden-identifiers

> Detect forbidden words in Rust identifiers. Scan all Rust source files under `src/`.
> Codes: `FBD001`-`FBD013` | `--fix`: unsupported (check only)

## Goal

Split identifiers into segments (underscores / PascalCase) and report matches against the forbidden-word list; check the `target_` prefix separately.

## Segmentation algorithm

- Names containing `_`: `name.lower().split("_")`.
- Otherwise split PascalCase/camelCase at capitals: `IOError` -> `["io", "error"]`, `ParseErr` -> `["parse", "err"]`, `XMLParser` -> `["xml", "parser"]`.

## Context labels

Each identifier definition has a context. Most rules exempt types (`type`) and fields (`field`):

`function`, `let` (bindings), `parameter`, `const`, `static`, `enum_variant`,
`type` (struct/enum/type/trait/union names), `field` (field declarations), and `macro_field` (structured macro field keys such as `tracing::warn!(error_variant = ?e)`).

## Global exclusions

- Identifiers in inline `#[cfg(test)]` modules are skipped.
- Separate test files declared by `#[cfg(test)] mod <name>;`, `tests.rs`, and files with `tests` in their path are skipped entirely.
- `exclude_filenames`: matching basenames are excluded.
- `skip_module_paths`: skip the entire file when its relative path contains any listed string.
- `allowed_modules`: exempt the specified `crate::a::b` module path and all descendants.
- `ignore_files`: skip matching resolved absolute paths.

---

## Rules

### FBD001 - Forbidden word `result`

> Message: `'{name}' contains forbidden word 'result'`

A segment is `result` and the context is not type/field.

```rust
// BAD
fn parse_result() {}

// GOOD
let prev_value = 1;
```

### FBD002 - Forbidden abbreviation `res`

> Message: `'{name}' contains forbidden word 'res'`

A segment is `res` and the context is not type/field.

```rust
// BAD
fn compute_res() {}
```

### FBD003 - `error` is forbidden (configured by `words`)

> Message: `'{name}' uses 'error'; use 'err' instead`

A segment is `error` and the context is not type/field. This check precedes `err` and returns on a match, skipping FBD004 and later checks.
The `macro_field` context checks **only** the `error` segment, not `result`, `res`, `err`, etc.
Type and field names are exempt from `error`: `struct MyError` and `field error_count: u32` are allowed.

```rust
// BAD - Five occurrences
fn handle_error() {}
fn process() {
    let error_msg = "";      // let
    let error = MyError;     // Bare error
}
const MAX_ERROR: u32 = 0;    // const
static ERROR_CODE: u32 = 0;  // static
```

```rust
// macro_field: only error_variant is reported; err_message is not.
fn process() {
    tracing::warn!(
        error_variant = ?SomeError,   // FBD003
        err_message = %message,       // Not reported: macro_field only checks error.
        "failed",
    );
}
```

### FBD004 - Context-dependent `err` placement (configured by `contextual_word`)

> Message (function names): `'{name}' - 'err' in function names only allowed as '_err' suffix`
> Message (let/parameters): `'{name}' - 'err' in local variables only allowed as 'err_' prefix on non-Error types; explicit Error instantiation is forbidden`
> Message (const/static/enum_variant): `'{name}' - 'err' is forbidden in this context`

For contexts other than type/field, three cases apply:

**4a Function names**: allow only an `_err` **suffix**, with at least two segments. `parse_err` is allowed; `err_handler` (prefix),
`err` (bare), and `process_err_data` (middle segment) are forbidden.

**4b Let bindings / parameters**: allow only an `err_` **prefix** (at least two segments, `err` first) whose bound type is **not an Error type**.
`err_code: u32` is allowed; `err_code: SomeError` is forbidden. Error-type detection covers explicit annotations, struct expressions
(`SomeError { ... }`), calls (`SomeError::new()`), field expressions, macro invocations, bare identifiers (`let e = SomeError;`),
scoped paths (`std::io::Error`), and one level inside `if`/`match`/`closure` expressions.

**4c Const / static / enum_variant**: `err` is always forbidden. `ERR_CODE`, `GLOBAL_ERR`, and `ErrVariant` are violations.

```rust
// GOOD
fn parse_err() -> Result<(), ()> { Ok(()) }   // Function _err suffix
fn process() {
    let err_code: u32 = 5;                     // err_ prefix, non-Error type
    let err_msg = String::new();
}
fn process(err_code: u32) {}                   // Parameter err_ prefix

// BAD
fn err_handler() {}                            // Function err_ prefix
fn err() {}                                    // Bare function name err
fn process() {
    let err = 42;             // Bare err
    let parse_err = 42;       // _err suffix on a let binding
    let err_code = SomeError; // err_ prefix, but an Error type
    let err_msg = SomeError::new();
    let err_info = ParseError { code: 1 };
    let err_val = if cond { SomeError } else { OtherError };
    let err_out = match cond { true => SomeError::new(), _ => OtherError };
    let err_fn = || SomeError::new();
}
const ERR_CODE: u32 = 0;      // const
static GLOBAL_ERR: u32 = 0;   // static
```

`SomeError` and `ParseError` themselves are not reported in these fixtures: type names are exempt from FBD003/FBD004.

### FBD005 - Forbidden word `closure`

> Message: `'{name}' contains forbidden word 'closure'`

A segment is `closure` and the context is not type/field.

```rust
// BAD
fn get_closure() {}
```

### FBD006 - Forbidden word `connection`

> Message: `'{name}' uses 'connection'; use 'conn' instead`

A segment is `connection` and the context is not type/field.

```rust
// BAD
fn open_connection() {}

// GOOD
pub struct ConnInfo {
    pub db_conn: String,
}
```

### FBD007 - Forbidden abbreviation `txn`

> Message: `'{name}' contains forbidden word 'txn'`

A segment is `txn` and the context is not type/field.

```rust
// BAD
fn begin_txn() {}
```

### FBD008 - Forbidden abbreviation `tx`

> Message: `'{name}' contains forbidden word 'tx'`

A segment is `tx` and the context is not type/field.

```rust
// BAD
fn commit_tx() {}
```

### FBD009 - Forbidden `target_` prefix (configured by `prefixes`)

> Message: `'{name}' starts with forbidden 'target_' prefix`

The original name starts with the literal `target_`, checked before segmentation, in a non-type/field context. This is the first check and returns on a match.
Type and field names may start with `target_`.

```rust
// BAD
static target_name: &str = "";
static target_x: u8 = 0;
```

### FBD010 - Forbidden word `extension`

> Message: `'{name}' uses 'extension'; use 'ext' instead`

A segment is `extension`. **This is the only default word checked in type and field contexts**; other words are silent for those contexts.
Thus `struct ExtensionHandler` and `field file_extension` also trigger the rule.

```rust
// BAD
fn f9(extension: ()) {}
struct ExtensionHandler;   // Also reported
```

### FBD011 - Forbidden word `previous`

> Message: `'{name}' uses 'previous'; use 'prev' instead`

A segment is `previous` and the context is not type/field. PascalCase is also checked: `PreviousValue` -> `["previous", "value"]`.

```rust
// BAD
fn read_previous() {}
fn f10(previous_value: ()) {}
fn f11(PreviousValue: ()) {}   // PascalCase is also reported.

// GOOD
fn read_prev() {}
```

### FBD012 - Abbreviate `replacements` as `repl`

> Message: `'{name}' uses 'replacements'; use 'repl' instead`

### FBD013 - Abbreviate `current` as `curr`

> Message: `'{name}' uses 'current'; use 'curr' instead`

## Full code coverage (self-test assertions)

```rust
fn f1(result: ()) {}          // FBD001
fn f2(res: ()) {}             // FBD002
fn f3(error: ()) {}           // FBD003
fn f4(err: ()) {}             // FBD004
fn f5(closure: ()) {}         // FBD005
fn f6(connection: ()) {}      // FBD006
fn f7(txn: ()) {}             // FBD007
fn f8(tx: ()) {}              // FBD008
static target_x: u8 = 0;      // FBD009
fn f9(extension: ()) {}       // FBD010
fn f10(previous_value: ()) {} // FBD011
fn f11(PreviousValue: ()) {}  // FBD011 (PascalCase)
fn f12(replacements: ()) {}   // FBD012
fn f13(current: ()) {}        // FBD013
fn f14(prev_value: (), repl: (), curr: (), msg: ()) {} // Allowed alternatives
```

Expected coverage: `FBD001` through `FBD013`; FBD011 occurs in both snake_case and PascalCase, for 14 total diagnostics.

## Configuration

| Key | Description | Default |
| --- | --- | --- |
| `words` | Forbidden segments; entries contain `word`, `code`, `replacement`, `contexts`, and optional `allowed_modules` | See `defaults.toml` |
| `prefixes` | Forbidden prefixes; entries contain `prefix`, `code`, `message`, and `contexts`. Messages support `{name}`, `{prefix}`, and `{context}` | `target_` (FBD009) |
| `contextual_word` | Word with context-specific placement rules, diagnostic code, applicable contexts, allowed positions, Error-type markers, and three message templates | `err` (FBD004) |
| `allowed_modules` | Globally exempt module paths, including descendants | `[]` |
| `skip_module_paths` | Skip the file if its relative path contains a listed string | `[]` |
| `ignore_files` | Skip matching resolved absolute paths | `[]` |
| `exclude_filenames` | Exclude matching basenames | `["schema.rs"]` |

The `[forbidden-identifiers]` section replaces the entire default section: define the complete rules, not an increment. An explicitly empty section disables all configured rules for this checker.

`replacement = ""` forbids the word itself and produces
`'{name}' contains forbidden word '{word}'`; a nonempty replacement means the spelling is nonstandard and supplies the preferred alternative.
Per-word `allowed_modules` exempts only that word; global `allowed_modules` exempts all rules in the checker.
