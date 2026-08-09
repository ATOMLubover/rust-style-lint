# no-unnamed-fields

> 禁止 enum 中的元组型（unnamed）变体；变体字段必须全部命名。
> 代码：`ENUM001` ｜ `--fix`：不支持（仅检测）

## 目标

**所有 enum 变体只能有 named fields**。每个字段都必须有一个表达语义的名字；
元组型 `Variant(String, u32)` 把字段位置化，读者必须猜第 2 个字段是什么。

Unit 变体（无字段）与 struct 变体（`{ name: Type }` 命名字段）都符合规则，不报错。

```rust
enum Error {
    Model(String, u32),            // BAD — 未命名字段
    Empty(),                       // BAD — 空元组同样禁止
    Unit,                          // GOOD — 无字段
    Model { message: String },     // GOOD — 命名字段
    Model { kind: u32 },           // GOOD — 命名字段
}
```

## 触发条件

AST 出现 `enum_variant` 节点，且其 named_children 中存在
`ordered_field_declaration_list` —— 即 `Variant(...)` 元组形态。

- 单字段 `Variant(u8)`、多字段 `Variant(String, u32)`、空元组 `Variant()` **一律触发**。
- 显式判别值 `Variant = 5`（子节点为 `integer_literal`）**不触发**。
- `Variant { x: u8 }`（`field_declaration_list`）与 unit `Variant` **不触发**。

> message：`enum variant `{name}` has unnamed fields; give every field a name or use a unit variant`
> `{name}` 是变体的名字（`name` 字段的文本）。

## 违规（BAD）

```rust
pub enum ModelError {
    Model(String, u32),
}
```

```rust
pub enum ApiError {
    NotFound,
    BadRequest(String),
    Timeout(u64),
}
```

单字段与空元组同样报 `ENUM001`：

```rust
pub enum Token {
    Value(String),   // BAD — 单字段也必须命名
    Empty(),         // BAD — 空元组无意义，应为 unit 变体
}
```

## 符合（GOOD）

```rust
pub enum ModelError {
    Model { message: String, code: u32 },
}
```

```rust
pub enum ApiError {
    NotFound,
    BadRequest { detail: String },
    Timeout { after_ms: u64 },
}
```

```rust
pub enum Token {
    Value { text: String },
    Missing,
}
```

> 命名是语义决策（字段叫什么、保留哪些字段），无法机械推导，因此只报告不自动改写。

## 配置

此 checker **不读取任何配置键**。`defaults.toml` 里没有对应段。
