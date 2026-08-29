# spacing-style

> 自定义 Rust 块间距规则：声明/块起始分隔符、语句/match arm/enum variant/模块项之间的空行。
> 代码：`BLK000`–`BLK003`、`PARSE001` ｜ `--fix`：BLK000/BLK001/BLK002/BLK003 支持

## 目标

强制统一的排版：

1. 多语句/多 match arm 的块，以及多成员 enum/struct 声明，起始 `{` 后必须有一个裸 `//` 分隔符。
2. 直接相邻的语句、match arm、enum variant 之间必须有空行。
3. struct 字面量和非 struct 声明字段列表禁止裸 `//`；单单位紧凑块或声明里的裸 `//` 是冗余的。
4. 模块作用域内的项（struct/impl/trait/fn/mod/use/const/static/type/…）之间必须有空行。

## 裸 `//` 分隔符（bare separator）是什么

匹配 `^\s*//\s*$` 的行——整行只有 `//`（可带前后空白）。纯视觉分割线。
与之相对的是"真注释"（含实际文本的 `// 注释`）。

函数和 match 块起始处的真注释可以满足 BLK000；enum/struct 声明始终要求真正的裸 `//`，
`///` 或有内容的 `//` 不能替代。

## BLK000 — 块起始缺裸 `//` 分隔符

> message：`{description} whose opening brace is not on its own line requires a bare // separator before its first {kind}`
> `description` 取值：`multi-{kind} block`（≥2 个单位）或 `multi-line {kind} block`（单单位跨多行）
> `kind` 取值：`statement`、`match arm`、`enum variant`、`struct field`

### 触发条件

容器是 `block`、`match_block`、enum 的 `enum_variant_list`，或父节点为 `struct_item` 的
`field_declaration_list`，且：

- `{` 不是单独独占一行；且
- （单位数 ≥ 2 或 首个单位跨多行）；且
- `{` 与首个单位之间没有裸 `//` 分隔符；且
- 对函数/match 块，也没有真注释；enum/struct 声明不应用此豁免。

### 违规（BAD）

```rust
if condition {
    statement_1();

    statement_2();
}
```

单单位跨多行同样要分隔符（如块内一个跨多行的 `match` 表达式）。

enum/struct 声明即使首成员有文档注释，也必须在文档注释前保留裸分隔符：

```rust
struct Payload {
    //
    /// Stored payload content.
    content: String,

    size: usize,
}
```

### 符合（GOOD）

```rust
if condition {
    //
    statement_1();

    statement_2();
}
```

真注释也满足分隔需求：

```rust
if condition {
    // Set up the initial state
    do_first();
    do_second();
}
```

### 豁免

- `{` 单独独占一行（`if condition\n{`）——不需要分隔符。
- 单语句且单行能放下的紧凑块——不强制。
- 单成员、单行的 enum/struct 声明——不要求分隔符。
- struct 字面量、union 和 enum 结构体 variant 内部字段列表——不要求分隔符。

### --fix

插入一行 `//`。两种形式：(a) 首单位在后续行时，在 `{` 下一行插入与首单位同缩进的 `//`；
(b) 极端内联形式 `{ first; second; }` 时，拆行插入 `//` 并重新缩进首单位。

## BLK001 — 单位间缺空行

> message：`missing blank line before this {kind}; previous {kind} ended at line {line_number}`
> `kind` 取值：`statement`、`match arm`、`enum variant`

### 触发条件

`block` / `match_block` / `enum_variant_list` 容器内两个连续直接单位：

- 不在同一行（`previous.end_point.row != current.start_point.row`）；且
- 前一个单位结束与当前单位开始之间没有任何全空行。

### 违规（BAD）

```rust
enum Payload {
    /// First payload.
    First,
    /// Second payload.
    Second,
}
```

### 符合（GOOD）

```rust
enum Payload {
    /// First payload.
    First,

    /// Second payload.
    Second,
}
```

语句间同理：

```rust
fn example() {
    //
    let router = router();

    #[cfg(feature = "swagger-ui")]
    let router = router.merge(swagger());

    router
}
```

### 豁免

- 同行语句（用 `;` 分隔）——不查。
- struct 字段——不查空行。

### --fix

在当前单位前插入空行。若两个语句之间有解释性注释，空行插在**注释前**，让注释保持附着在其描述的语句上。

## BLK002 — 冗余裸 `//` 分隔符

### 变体一：不使用声明分隔符的字段列表

> message：`bare // separator is forbidden in this field list; fields here need no separator`

struct 字面量的 `field_initializer_list`，以及 union/enum 结构体 variant 的
`field_declaration_list`，在 `{` 与首个字段之间存在裸 `//`。

```rust
// BAD
let payload = Payload {
    //
    first: String::new(),
    second: String::new(),
};

// GOOD
let payload = Payload {
    first: String::new(),
    second: String::new(),
};
```

外层函数块的裸 `//`（`block` 容器）不被此变体标记，只删除字段列表里的分隔符。

### 变体二：单语句块里的冗余分隔符

> message：`bare // block-start separator is redundant in a single-statement block`

`block` / `match_block` 恰好 1 个单位、单行、且 `{` 与单位之间有裸 `//`。

```rust
// BAD
if condition {
    //
    return;
}

// GOOD
if condition {
    return;
}
```

### --fix

删除裸 `//` 行。若 `{` 与单位之间只有空行和裸 `//`，全部删掉；若有真注释或其他内容，只删裸 `//` 行。

## BLK003 — 项之间缺空行

> message：`missing blank line before this {kind}; previous {kind} ended at line {line_number}`
> `kind` 取值：`struct`、`impl block`、`trait`、`function`、`module`、`use declaration`、`constant`、`static`、`type alias`、`enum`、`union`、`macro definition`、`macro invocation`、`extern crate`、`extern block` 等

### 触发条件

模块作用域（顶层 `source_file` 与内联 `mod { … }` 的 `declaration_list`）内，两个连续的直接"项"
之间没有空行。项 = struct/enum/union/impl/trait/fn/mod/use/const/static/type/macro_rules!/宏调用/extern crate/extern 块等。

### 违规（BAD）

```rust
use crate::result::BaseRest;
/// Constraints bound into a presigned image upload request.
pub struct ImageUploadSpec<'a> {
    pub object_key: &'a str,
}
```

### 符合（GOOD）

```rust
use crate::result::BaseRest;

/// Constraints bound into a presigned image upload request.
pub struct ImageUploadSpec<'a> {
    pub object_key: &'a str,
}
```

### 豁免

- 容器内第一个项——前面没有东西可比。
- 同行的两个项（`fn a() {} fn b() {}`）——不查。
- 连续的同类型 header 项：`use`↔`use`、`mod`↔`mod`——不强制空行，内部分组交给 use-style 管理。
- `impl`/`trait`/`extern` 体内的 method——不查（本规则只到模块作用域）。

### 锚点（anchor）

项的前导注释/属性（`///`、`//`、`#[...]`）随项一起移动：空行要求落在整组之前，缺失时在
注释/属性行首插入空行，注释保持附着在其描述的项上。

### --fix

在当前项（或它的前导注释/属性）的行首插入一个空行。

## PARSE001 — Rust 语法解析错误（软警告）

> message：`Rust syntax tree contains {node.type!r}; spacing results near this location may be incomplete`

tree-sitter AST 里出现 `ERROR` 节点或 `is_missing` 节点时提示。**无 fix**——解析错误的文件在 fix 时整体跳过，避免基于不完整节点范围做错误编辑。

## 宏体（macro body）检查

tree-sitter 把宏调用体解析成不透明的 `token_tree`，里面**没有** `block` / `match_block`
节点——所以只查真实容器的话，`tokio::select!` 等宏体内的 spacing 问题完全查不到。

此 checker 会**下沉进宏体**，复刻 rustfmt 的两级策略：

1. **Tier 1（按 Rust 解析）**：取宏体花括号内层字节单独重解析，若**无 `ERROR` 节点**
   （arm 体、内层 `match`、`else` 块、`return;`/`break;` 等），按完整规则跑 BLK000/BLK001/BLK002
   ——含**语句级**空行检查，不只限 match arm。位置经字节偏移映射回原文件。
2. **Tier 2（`=>` match-like 启发式）**：重解析失败的是自定义语法（select! 的
   `pattern = expr, if guard => body`、裸 match arm 列表等），在 token 层按顶层 `=>` 切分 arm，
   报 arm 级 BLK000/BLK001。按 `=>` 切分而非逗号——`if guard` 的逗号属于 arm 内部。

宏体内的诊断一律是 **`warning`**（不 fail 退出码），message 追加 `(macro body)` 后缀，
且**永不 `--fix`**——宏体是自定义语法，不能安全改写，须按规则手改。

跳过：`macro_rules!` 定义体（`macro_definition`）、`(...)` / `[...]` 定界的宏
（`vec!`、`format!`、`println!` 等）。

```rust
// BAD —— 全在宏体内，报 warning
tokio::select! {
    command = recv.recv() => {
        handle();
    }
    task = recv2.recv() => {
        handle2();
    }
}

// GOOD
tokio::select! {
    //
    command = recv.recv() => {
        handle();
    }

    task = recv2.recv() => {
        handle2();
    }
}
```

## 架构要点（了解即可）

- 外层属性（`#[...]`）与其后的语句组成**一个单位**，不单独计数。`#[cfg(...)]\nlet x = ...` 是一个单位，不会误报 BLK001。
- 两个 checker 都用 `production_source()` 掩码 `#[cfg(test)]` 模块和 `tests/` 目录——测试代码对间距检查不可见。

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `ignore_dirs` | 目录名列表，`.rs` 文件发现时跳过（路径任意一段命中即忽略） | `[".git", ".hg", ".svn", ".idea", ".vscode", "target", "node_modules"]` |
