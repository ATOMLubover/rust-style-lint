# forbidden-identifiers

> 检测 Rust 标识符名里的禁用词。扫描 `src/` 下所有 Rust 源文件。
> 代码：`FBD001`–`FBD013` ｜ `--fix`：不支持（仅检测）

## 目标

标识符按段拆分（下划线拆分 / PascalCase 拆分）后，命中禁用词表即报告；同时单独标记 `target_` 前缀。

## 拆分算法

- 含 `_`：`name.lower().split("_")`。
- 否则 PascalCase/camelCase 按大写拆分：`IOError` → `["io", "error"]`、`ParseErr` → `["parse", "err"]`、`XMLParser` → `["xml", "parser"]`。

## 上下文标签

每个标识符定义带上下文，多数规则对类型（`type`）和字段（`field`）上下文豁免：

`function`（函数）、`let`（绑定）、`parameter`（参数）、`const`、`static`、`enum_variant`、
`type`（struct/enum/type/trait/union 名）、`field`（字段声明）、`macro_field`（结构化宏调用字段键，如 `tracing::warn!(error_variant = ?e)`）。

## 全局跳过逻辑

- `#[cfg(test)]` 内联模块里的标识符：跳过。
- `#[cfg(test)] mod <name>;` 声明的独立测试文件（及 `tests.rs`、路径含 `tests` 的文件）：整文件跳过。
- `exclude_filenames`：basename 命中即排除。
- `skip_module_paths`：相对路径包含任一字符串即跳过整文件。
- `allowed_modules`：按 `crate::a::b` module 路径豁免该 module 及其所有子 module。
- `ignore_files`：解析后的绝对路径命中即跳过。

---

## 子规则清单

### FBD001 — 禁用词 `result`

> message：`'{name}' contains forbidden word 'result'`

段含 `result`，且上下文非 type/field。

```rust
// BAD
fn parse_result() {}

// GOOD
let prev_value = 1;
```

### FBD002 — 禁用缩写 `res`

> message：`'{name}' contains forbidden word 'res'`

段含 `res`，上下文非 type/field。

```rust
// BAD
fn compute_res() {}
```

### FBD003 — `error` 永远禁用（由 `words` 配置）

> message：`'{name}' uses 'error'; use 'err' instead`

段含 `error`，上下文非 type/field。此检查在 `err` 检查**之前**，命中即返回（不再查 FBD004 等）。
`macro_field` 上下文**只**查 `error` 段——`result`/`res`/`err` 等不查。
类型名和字段不查 `error`：`struct MyError`、`field error_count: u32` 都允许。

```rust
// BAD —— 5 处
fn handle_error() {}
fn process() {
    let error_msg = "";      // let
    let error = MyError;     // 裸 error
}
const MAX_ERROR: u32 = 0;    // const
static ERROR_CODE: u32 = 0;  // static
```

```rust
// macro_field：只有 error_variant 报；err_message 不报
fn process() {
    tracing::warn!(
        error_variant = ?SomeError,   // FBD003
        err_message = %message,       // 不报——macro_field 只查 error
        "failed",
    );
}
```

### FBD004 — `err` 形式按上下文受限（由 `contextual_word` 配置）

> message（函数名）：`'{name}' — 'err' in function names only allowed as '_err' suffix`
> message（let/参数）：`'{name}' — 'err' in local variables only allowed as 'err_' prefix on non-Error types; explicit Error instantiation is forbidden`
> message（const/static/enum_variant）：`'{name}' — 'err' is forbidden in this context`

上下文非 type/field 时按三种情况：

**4a 函数名**：只允许 `_err` **后缀**且至少 2 段。`parse_err` 允许；`err_handler`（前缀）、
`err`（裸）、`process_err_data`（中间段）都禁止。

**4b let 绑定 / 参数**：只允许 `err_` **前缀**（至少 2 段，`err` 是首段）且绑定类型**不是 Error 类型**。
`err_code: u32` 允许；`err_code: SomeError` 禁止。Error 类型检测覆盖：显式类型注解、struct 表达式
（`SomeError { ... }`）、调用表达式（`SomeError::new()`）、字段表达式、宏调用、裸标识符（`let e = SomeError;`）、
作用域路径（`std::io::Error`），并向下钻一层 `if`/`match`/`closure` 表达式。

**4c const / static / enum_variant**：`err` 永远禁止。`ERR_CODE`、`GLOBAL_ERR`、`ErrVariant` 都违规。

```rust
// GOOD
fn parse_err() -> Result<(), ()> { Ok(()) }   // 函数 _err 后缀
fn process() {
    let err_code: u32 = 5;                     // err_ 前缀，非 Error 类型
    let err_msg = String::new();
}
fn process(err_code: u32) {}                   // 参数 err_ 前缀

// BAD
fn err_handler() {}                            // 函数 err_ 前缀
fn err() {}                                    // 函数裸 err
fn process() {
    let err = 42;             // 裸 err
    let parse_err = 42;       // _err 后缀在 let
    let err_code = SomeError; // err_ 前缀但 Error 类型
    let err_msg = SomeError::new();
    let err_info = ParseError { code: 1 };
    let err_val = if cond { SomeError } else { OtherError };
    let err_out = match cond { true => SomeError::new(), _ => OtherError };
    let err_fn = || SomeError::new();
}
const ERR_CODE: u32 = 0;      // const
static GLOBAL_ERR: u32 = 0;   // static
```

注意：上述 fixture 里的 `SomeError`、`ParseError` 本身不报——它们是类型名，type 上下文豁免 FBD003/FBD004。

### FBD005 — 禁用词 `closure`

> message：`'{name}' contains forbidden word 'closure'`

段含 `closure`，上下文非 type/field。

```rust
// BAD
fn get_closure() {}
```

### FBD006 — 禁用词 `connection`

> message：`'{name}' uses 'connection'; use 'conn' instead`

段含 `connection`，上下文非 type/field。

```rust
// BAD
fn open_connection() {}

// GOOD
pub struct ConnInfo {
    pub db_conn: String,
}
```

### FBD007 — 禁用缩写 `txn`

> message：`'{name}' contains forbidden word 'txn'`

段含 `txn`，上下文非 type/field。

```rust
// BAD
fn begin_txn() {}
```

### FBD008 — 禁用缩写 `tx`

> message：`'{name}' contains forbidden word 'tx'`

段含 `tx`，上下文非 type/field。

```rust
// BAD
fn commit_tx() {}
```

### FBD009 — 禁用 `target_` 前缀（由 `prefixes` 配置）

> message：`'{name}' starts with forbidden 'target_' prefix`

原始名字以字面 `target_` 开头（先于段拆分检查），上下文非 type/field。此检查最早执行，命中即返回。
类型名和字段名允许以 `target_` 开头。

```rust
// BAD
static target_name: &str = "";
static target_x: u8 = 0;
```

### FBD010 — 禁用词 `extension`

> message：`'{name}' uses 'extension'; use 'ext' instead`

段含 `extension`。**这是唯一在 type 和 field 上下文也检查的默认词**——其他词对类型/字段静默。
所以 `struct ExtensionHandler`、`field file_extension` 也会报。

```rust
// BAD
fn f9(extension: ()) {}
struct ExtensionHandler;   // 也报
```

### FBD011 — 禁用词 `previous`

> message：`'{name}' uses 'previous'; use 'prev' instead`

段含 `previous`，上下文非 type/field。PascalCase 也查：`PreviousValue` → `["previous", "value"]`。

```rust
// BAD
fn read_previous() {}
fn f10(previous_value: ()) {}
fn f11(PreviousValue: ()) {}   // PascalCase 也报

// GOOD
fn read_prev() {}
```

### FBD012 — `replacements` 应缩写为 `repl`

> message：`'{name}' uses 'replacements'; use 'repl' instead`

### FBD013 — `current` 应缩写为 `curr`

> message：`'{name}' uses 'current'; use 'curr' instead`

## 全 code 覆盖验证（self-test 断言）

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
fn f11(PreviousValue: ()) {}  // FBD011（PascalCase）
fn f12(replacements: ()) {}   // FBD012
fn f13(current: ()) {}        // FBD013
fn f14(prev_value: (), repl: (), curr: (), msg: ()) {} // 允许的替代
```

预期覆盖 `FBD001` 至 `FBD013`；`FBD011` 因 snake_case/PascalCase 各一处，共 14 处。

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `words` | 禁用段列表；每项包含 `word`、`code`、`replacement`、`contexts`，可选 `allowed_modules` | 见 `defaults.toml` |
| `prefixes` | 禁用前缀列表；每项包含 `prefix`、`code`、`message`、`contexts`。消息支持 `{name}`、`{prefix}`、`{context}` | `target_`（FBD009） |
| `contextual_word` | 按上下文限制位置的词，包含错误码、适用上下文、允许位置、Error 类型识别词及三类消息模板 | `err`（FBD004） |
| `allowed_modules` | 全局豁免的 module 路径；同时豁免其子 module | `[]` |
| `skip_module_paths` | 相对路径包含任一字符串即跳过整文件 | `[]` |
| `ignore_files` | 解析后的绝对路径命中即跳过 | `[]` |
| `exclude_filenames` | basename 命中即排除 | `["schema.rs"]` |

`[forbidden-identifiers]` 段整段覆盖 defaults——定义完整规则，不是增量。显式空段会关闭该 checker 的全部配置规则。

`replacement = ""` 表示该词本身禁止出现，诊断为
`'{name}' contains forbidden word '{word}'`；非空表示该拼写不符合规范，诊断会给出建议写法。
每条 word 的 `allowed_modules` 只豁免该词，全局 `allowed_modules` 则豁免 checker 的全部规则。
