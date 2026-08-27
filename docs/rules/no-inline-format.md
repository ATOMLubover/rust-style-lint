# no-inline-format

> 禁止 format 字符串里的内联具名捕获 —— 所有参数必须位置化传参。
> 代码：`FMT001` ｜ `--fix`：不支持（仅检测）

## 目标

`format!("hello, {name}")` 这类内联捕获（Rust 1.58 起可用）必须改写为位置参数
`format!("hello, {}", name)`。格式字符串里的 `{identifier}`（identifier 以字母或
下划线开头）都是违规，参数必须出现在参数列表里。

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

## 触发条件（全部满足才报）

1. AST 节点是 `macro_invocation`，宏名在 `macros` 列表里（见[配置](#配置)）。
2. token tree 里 format 字符串**必须是该位置的字符串字面量**：
   - `format!`、`format_args!`、`print!`、`println!`、`eprint!`、`eprintln!`、`panic!`
     取**第一个**实参；
   - `write!`、`writeln!` 取**第二个**实参（第一个是 writer）。
   - 若该位置不是字面量（是变量、`concat!(...)` 调用等），无法静态判定 format
     字符串，**跳过不报**。
   - 更靠后的字符串字面量是数据参数，不是 format 字符串，不查。
3. format 字符串里扫描到内联捕获 `{name}`（可带格式说明符，如 `{name:?}`、`{name:.2}`）。

`{{`、`}}` 是转义花括号（字面量 `{`、`}`），**不是**捕获；`{}`、`{0}`（索引）以及
只有格式说明符的 `{:?}`、`{:<10}` 都是合法的位置化用法，不报。

## 违规（BAD）

```rust
let name = "x";
format!("hello, {name}");                    // {name}
println!("x={x}, y={y}", x = 1, y = 2);      // {x} {y}
write!(f, "{value:?}", value);               // {value:?}
panic!("err: {errno}", errno);               // {errno}
format!(r#"raw {n}"#, n = 1);                // 原始字符串同样查
```

> message：`format string uses inline capture '{name}'; pass 'name' positionally to {实际宏名}!`
>
> 同一 format 字符串里每个不同的捕获名各报一次。

## 符合（GOOD）

```rust
let name = "x";
format!("hello, {}", name);                  // 位置参数
println!("{0}/{1}", 1, 2);                   // 索引
println!("spec: {:?} {:.2}", name, 3.14);    // 只有格式说明符
println!("literal braces: {{name}}");        // 转义花括号是字面量
let msg = "{name}";                          // 数据字符串，不是 format 字符串
format!(msg, name);                          // format 字符串不是字面量，跳过
format!("{}", msg);                          // "{name}" 是数据参数，不查
write!(get_writer(), "{}", w);               // writer 不是字面量，format 字符串仍是第二个
```

## 配置

`defaults.toml` 里的 `[no-inline-format].macros` 定义格式字符串位于第一参数的宏，
`writer_macros` 定义 writer 位于第一参数、格式字符串位于第二参数的宏
（`write!`/`writeln!` 取第二个实参，其余取第一个）：

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

项目在 `rust-style-lint.toml` 里定义 `[no-inline-format]` 段会**整体替换**该表
（与其它 checker 的替换语义一致）。默认段完整可用，通常无需配置。
