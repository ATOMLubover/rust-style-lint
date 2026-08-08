# doc-comment-coverage

> 用户自定义标识符必须带指定风格的注释：公开项要外层 doc 注释（`///` / `/**`），私有项要普通注释（`//` / `/*`）。
> 代码：`DOC001` ｜ `--fix`：不支持（仅检测）

## 目标

扫描 `src/` 下所有 Rust 源文件，报告没有紧邻前置注释的声明：

- **公开项**需要外层 doc 注释（`///` 或 `/**`）；
- **私有项**需要普通注释（`//` 或 `/*`）。

## 被覆盖的声明种类（14 种 AST 节点）

模块级项：模块声明、函数、struct、enum、trait、类型别名、const、static、宏定义、union。
trait 成员：trait 定义内的关联函数、类型别名、常量（隐式公开）。
enum variant：每个变体（隐式公开）。
固有方法：impl 块里的公开函数。

节点类型清单：`associated_type`、`const_item`、`enum_item`、`enum_variant`、`field_declaration`、
`function_item`、`function_signature_item`、`macro_definition`、`mod_item`、`static_item`、
`struct_item`、`trait_item`、`type_item`、`union_item`。

## DOC001 — 缺失要求的注释

> message（动态拼接）：`{visibility} {declaration type} '{name}' is missing a {comment_kind}`
> 如：`public function item 'undocumented_fn' is missing a doc comment`
> `comment_kind`：公开项为 `doc comment`，私有项为 `regular comment`

### 触发条件（全部满足）

1. 文件在 `src/` 下且未被排除（见配置）。
2. 节点属于上述 14 种之一。
3. 不是测试项（`#[test]`、`#[tokio::test]`、`rstest` 任意位置出现都跳过）。
4. 不是 `src/main.rs` 里的 `main` 函数。
5. 节点有 `name` 字段。
6. 缺少要求的前置注释：

   - **公开项**：跳过 `attribute_item` 后往回看，第一个非属性兄弟必须是 `///` 开头
     的 `line_comment`，或 `/**` 开头的 `block_comment`。
   - **私有项**：第一个非属性兄弟必须是 `//` 开头（但**不是** `///`）且内容非空的 `line_comment`，
     或 `/*` 开头（但**不是** `/**`、**不是** `/*!`）且内容非空的 `block_comment`。
     裸 `//`（空内容）视为块分隔符，不算注释。

### 公开 vs 私有的判定

判为公开：带任何 `visibility_modifier`（`pub`、`pub(crate)` 等）；是 `enum_variant`；是外层 struct/enum/union
带可见性修饰的 `field_declaration`；嵌套在 `trait_item` 祖先内。
判为私有：在 `closure_expression` 或 `function_item` 祖先内（局部嵌套项）；且不满足任何公开条件。

### 违规（BAD）—— 9 处公开项

```rust
pub fn undocumented_fn() {}

pub struct UndocumentedStruct;

pub trait UndocumentedTrait {
    fn undocumented_trait_method(&self);
}

pub enum UndocumentedEnum {
    FirstVariant,
}

pub type UndocumentedType = u32;

/// This one is documented.
pub fn documented_fn() {}

// This is a regular comment, NOT a doc comment.
pub fn still_undocumented() {}

pub mod undocumented_mod;
```

私有项错误用 `///` 也算违规：

```rust
// BAD —— 2 处
fn uncommented_private_fn() {}

/// A doc comment is not a private implementation comment.
struct WronglyDocumentedPrivateStruct;
```

### 符合（GOOD）—— 公开项

```rust
/// A documented public function.
pub fn documented_fn() {}

/** A documented public struct. */
pub struct DocumentedStruct;

/// A documented trait.
pub trait DocumentedTrait {
    /// A documented trait method.
    fn trait_method(&self);
}

/// A documented enum.
pub enum DocumentedEnum {
    /// A documented variant.
    First,
}

/// A documented type alias.
pub type DocumentedType = u32;

/// A documented const.
pub const ANSWER: u32 = 42;

/// A documented module.
pub mod documented_mod;
```

私有项用普通注释：

```rust
// A documented implementation detail.
fn private_fn() {}

/* A private implementation type. */
struct PrivateStruct;

// A private module.
mod private_mod;
```

doc 注释和项之间可以有属性（跳过 `attribute_item`）：

```rust
/// Documented with an attribute in between.
#[derive(Debug)]
pub struct Attributed;
```

### 跳过的项

| 条件 | 机制 |
| --- | --- |
| `#[test]` / `#[tokio::test]` / `#[rstest::...]` 项 | `is_test_item()` 跳过 |
| `src/main.rs` 里的 `main` 函数 | `is_main_in_main_rs()` 跳过 |
| 内层 doc 注释（`//!`、`/*!`） | 它们记录的是外层模块，不是后续项；`/*!` 明确不算私有注释 |
| 裸 `//`（空内容） | 视为块分隔符 |
| 测试 fixture / 生成文件 | 按配置排除 |

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `exclude_segments` | 路径任一成分命中即跳过整个文件 | `["tests", "entity"]` |
| `exclude_filename_prefixes` | 路径任一成分以某前缀开头即跳过 | `["test_"]` |
| `exclude_filenames` | 精确文件名匹配即跳过 | `["schema.rs", "entity.rs"]` |
