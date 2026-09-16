# no-inline-format

> Forbid inline named captures in format strings; pass all arguments positionally.
> Code: `FMT001` | `--fix`: unsupported (check only)

## Goal

Rewrite inline captures such as `format!("hello, {name}")` (available since Rust 1.58) as positional arguments:
`format!("hello, {}", name)`. A format-string `{identifier}` whose identifier begins with a letter or
underscore is a violation; pass the argument in the argument list.

```rust
// BAD
format!("hello, {name}")
println!("x={x}, y={y}")
write!(f, "{value:?}", value)

// GOOD
format!("hello, {}", name)
println!("x={}, y={}", x, y)
write!(f, "{:?}", value)
```

## Trigger conditions (all required)

1. The AST node is a `macro_invocation` with a macro name in `macros` (see [configuration](#configuration)).
2. The format string in the token tree **must be a string literal at the designated position**:
   - `format!`, `format_args!`, `print!`, `println!`, `eprint!`, `eprintln!`, and `panic!`
     use the **first** argument.
   - `write!` and `writeln!` use the **second** argument; the first is the writer.
   - If that argument is not a literal (for example, a variable or `concat!(...)`), the format string cannot be determined statically
     and is **skipped without a diagnostic**.
   - Later string literals are data arguments, not the format string, and are not checked.
3. The format string contains an inline capture `{name}`, optionally with a format specifier such as `{name:?}` or `{name:.2}`.

`{{` and `}}` escape literal braces (`{` and `}`) and are **not** captures. `{}`, indexed `{0}`, and
specifier-only placeholders such as `{:?}` and `{:<10}` are valid positional forms and are not reported.

## Violations (BAD)

```rust
let name = "x";
format!("hello, {name}");                    // {name}
println!("x={x}, y={y}", x = 1, y = 2);      // {x} {y}
write!(f, "{value:?}", value);               // {value:?}
panic!("err: {errno}", errno);               // {errno}
format!(r#"raw {n}"#, n = 1);                // Raw strings are also checked.
```

> Message: `format string uses inline capture '{name}'; pass 'name' positionally to {actual_macro_name}!`
>
> Each distinct capture name in a format string is reported once.

## Compliant (GOOD)

```rust
let name = "x";
format!("hello, {}", name);                  // Positional argument
println!("{0}/{1}", 1, 2);                   // Indexed arguments
println!("spec: {:?} {:.2}", name, 3.14);    // Format specifiers only
println!("literal braces: {{name}}");        // Escaped braces are literal.
let msg = "{name}";                          // Data string, not a format string
format!(msg, name);                          // Non-literal format string: skipped
format!("{}", msg);                          // "{name}" is data and is not checked.
write!(get_writer(), "{}", w);               // The writer is not a literal; the format string is still second.
```

## Configuration

`[no-inline-format].macros` in `defaults.toml` lists macros whose format string is the first argument.
`writer_macros` lists macros whose first argument is the writer and whose second is the format string
(`write!`/`writeln!` use the second argument; the others use the first):

```toml
[no-inline-format]
macros = [
    "format",
    "format_args",
    "print",
    "println",
    "eprint",
    "eprintln",
    "panic",
]
writer_macros = ["write", "writeln"]
```

Defining `[no-inline-format]` in the project's `rust-style-lint.toml` **replaces the entire table**,
consistent with other checkers. The defaults are complete and usually need no customization.
