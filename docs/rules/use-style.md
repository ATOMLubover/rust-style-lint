# use-style

> 强制规范的模块级 Rust `use` 声明：分组、合并、排序、去重、`as _`、结构。
> 代码：`USE_*` 系列（14 条子规则）｜ `--fix`：大部分支持

## 目标

`use` 声明必须满足一套完整规范：叶子化 brace 树、按类别分组并排序、共享前缀合并、
去重、trait 用 `as _`、`use`/`pub use`/`mod`/`pub mod`/`mod tests` 结构顺序与空行。

## 类别与固定顺序

每个 `use` 叶子按根段分类：

| 根段 | 类别 |
| --- | --- |
| `super` | `super` |
| `std` | `std` |
| `crate` 或 `self` | `crate` |
| workspace 里的 crate 名（`workspace_crates`） | `local_crate` |
| 其他 | `third_party` |

组按此固定顺序输出：`super` → `std` → `third_party` → `local_crate` → `crate`。
组内按 kind 排序：`self`(0) → `name`(1) → `glob`(2)，然后按叶子名，再按别名。

---

## 子规则清单

### USE_BRACE_NON_LEAF — brace 树必须是叶子

> message：`brace items must be direct leaves, not nested paths`

`use foo::{...}` 的 `{}` 内每个子节点必须是直接叶子：`identifier`、`crate`、`self`、`super`、
裸 `*`、或 `use_as_clause`（目标是 `identifier` 或 `self`）。嵌套路径（`scoped_identifier` 或内层 `use_list`）违规。

```rust
// BAD
use std::{io::{Read, Write}};

// GOOD
use std::{io, mem};
```

**可 fix**（整块重写扁平化；若有解析错误则跳过修复）。

### USE_SUPER_OUTSIDE_TESTS — `super` 导入只允许在 mod tests 内

> message：`` `super` imports are only allowed inside mod tests ``

`use` 首段是 `super`，且词法上不在 `mod tests` 块内（文件名为 `tests.rs` 或路径含 `tests` 段也算在内）。

```rust
// BAD（模块作用域）
use super::Something;

// GOOD
#[cfg(test)]
mod tests {
    use super::*;
}
```

**不可 fix**（仅报错）。

### USE_VISIBILITY_BLANK_LINE — 普通 use 与 pub use 之间必须恰好一个空行

> message：`ordinary use and pub use blocks need exactly one blank line`

同一作用域内连续两个 `use_declaration`，一个 `pub` 一个非 `pub`，两者之间空白行数 ≠ 1。

```rust
// BAD
use std::io;
pub use crate::api;

// GOOD
use std::io;

pub use crate::api;
```

**可 fix**（通过结构修复器整块重写，块间用 `\n\n` 连接）。

### USE_ITEM_ORDER — 项顺序

> message：`private mod, pub mod, mod tests, ordinary use, pub use, then other code`

任意作用域内，语义子节点必须按 rank 升序：私有 `mod`=0、`pub mod`=1、`mod tests`=2、
普通 `use`=3、`pub use`=4、其他=5。某节点 rank 低于已出现的最高 rank 即违规。

```rust
// BAD
#[cfg(test)]
mod tests {
    use super::*;
}
mod before;
use std::time::Duration;
pub use crate::api::Api;
fn main_code() {}

// GOOD
mod before;

#[cfg(test)]
mod tests {
    use super::*;
}

use std::time::Duration;

pub use crate::api::Api;

fn main_code() {}
```

**可 fix**（结构修复器重新排序全部声明）。

### USE_MOD_GROUP_BLANK_LINE — mod 分组规则（三条，同一 code）

> message（同组相邻）：`mod declarations in the same block must be adjacent`
> message（cfg 块分隔）：`mod blocks with different cfg conditions must be separated by exactly one blank line`
> message（块间分隔）：`mod blocks must be separated by exactly one blank line`

同可见性类（私有 / pub / `mod tests`）内，相同 cfg 条件的连续 `mod_item` 不能有空白行；
cfg 条件不同则划为不同内部块，块间必须恰好一个空行。不同可见性块之间仍必须恰好一个空行。

```rust
// BAD —— cfg(all(...)) 和 cfg(...) 属于不同内部块
#[cfg(all(feature = "a", feature = "b"))]
mod both;
#[cfg(feature = "a")]
mod a;

// GOOD
#[cfg(all(feature = "a", feature = "b"))]
mod both;

#[cfg(feature = "a")]
mod a;
#[cfg(feature = "a")]
mod a_extra;
```

```rust
// BAD —— 同组有空白行
/// First module.
mod first;

/// Second module.
mod second;

#[cfg(test)]
mod tests {
    use super::*;
}

// GOOD
mod first;
/// Second module.
mod second;

#[cfg(test)]
mod tests {
    use super::*;
}
```

```rust
// BAD —— 块之间没有空行
/// Public module B.
pub mod public_b;
/// Private module A.
mod private_a;
#[cfg(test)]
mod tests {
    use super::*;
}
/// Public module A.
pub mod public_a;

// GOOD
mod private_a;

/// Public module B.
pub mod public_b;

/// Public module A.
pub mod public_a;

#[cfg(test)]
mod tests {
    use super::*;
}
```

**可 fix**（相同 cfg 的同组声明删空白行 / 不同 cfg 或可见性块间补一个空行）。

### USE_MULTIPLE_TEST_MODS — 每文件最多一个 `#[cfg(test)]` mod

> message：`only one #[cfg(test)] mod declaration is allowed per file; merge all tests into mod tests`

文件里带 `#[cfg(test)]` 的 `mod_item` 超过一个，第二个及之后都报。

```rust
// BAD
#[cfg(test)]
mod tests_a { }

#[cfg(test)]
mod tests_b { }

// GOOD
#[cfg(test)]
mod tests {
    // all tests here
}
```

**不可 fix**（仅报错）。

### USE_TRAIT_ALIAS_MISSING — trait 导入用 `as _`

> message：`` trait import `{full_path}` should use `as _` ``（如 `` trait import `anyhow::Context` should use `as _` ``）

`kind == "name"` 的叶子，`full_path` 在配置 `traits` 表里，别名不是 `_`，且 trait 名在非 use 源码里
没有被显式引用（`impl ... Name ... for`、`dyn Name`、`<... as Name>`、`where ...: Name`、
bound 位置（`:`, `+`, `<`, `,` 后）、`derive(...)` 内）。检查时 use 声明被掩码为空格。

```rust
// BAD
use anyhow::Context;

// GOOD
use anyhow::Context as _;
```

**可 fix**（别名改成 `_` 并重渲染）。

### USE_MIXED_GROUP — 一条 use 树不得混类别

> message：`one use tree must not mix import groups`

单条 `use` 声明的叶子属于多个类别。

```rust
// BAD
use std::io, crate::thing;

// GOOD
use std::{io, mem};
use crate::thing;
```

**可 fix**（重渲染时严格按类别分组）。

### USE_GROUP_ORDER — 组顺序

> message：`use group appears after a later group`

同一段（相同 cfg 条件和属性签名）内，连续 use 语句的类别必须按 `CATEGORIES` 非递减。
若某语句的最高类别索引小于之前已出现过的最高索引即违规。

```rust
// BAD —— crate(4) 在 std(1) 前
use crate::local;
use std::io;

// GOOD
use std::io;
use some_crate::Thing;
use crate::local;
```

**可 fix**（重渲染按 `CATEGORIES` 顺序重排）。

### USE_GROUP_BLANK_LINE — 不同 use 组之间恰好一个空行

> message：`different use groups need exactly one blank line`

同一段内两条连续 use 语句，类别集合不同（两边都非空），且两者之间字节间隙的换行数 ≠ 2（即不是恰好一个空行）。

```rust
// BAD
use std::io::Write;
use crate::local::Thing;

// GOOD
use std::io::Write;

use crate::local::Thing;
```

**可 fix**（重渲染时不同类别组间插 `\n\n`）。

### USE_CFG_BLOCK_BLANK_LINE — 不同 cfg 的 use 块之间恰好一个空行

> message：`use blocks with different cfg conditions must be separated by exactly one blank line`

同一 ordinary-use 或 pub-use 区域内，相邻声明的规范化 cfg 条件不同，二者之间必须恰好一个空行。
这里只划分 `use` 内部块，不改变 mod、use、其他 item 之间已有的顺序或空行规则。

```rust
// BAD
#[cfg(all(feature = "a", feature = "b"))]
use std::cmp::Ordering;
#[cfg(feature = "a")]
use std::mem::take;

// GOOD
#[cfg(all(feature = "a", feature = "b"))]
use std::cmp::Ordering;

#[cfg(feature = "a")]
use std::mem::take;
#[cfg(feature = "a")]
use std::time::Duration;
```

**可 fix**（结构修复器在 cfg 块之间插入 `\n\n`）。

### USE_DUPLICATE_IMPORT — 去重

> message：`duplicate imports must be merged`

别名化后，叶子 `(prefix, leaf, kind, alias)` 元组出现重复。

```rust
// BAD
#[cfg(feature = "a")]
use std::{mem::take, time};
#[cfg(feature = "a")]
use std::mem::take;

// GOOD（合并后）
#[cfg(feature = "a")]
use std::{mem::take, time};
```

**可 fix**（按 identity 去重后重渲染）。

### USE_MISSING_MERGE — 共享前缀必须合并成一条 brace 树

> message：`` imports under `{prefix}` must share one use tree ``（如 `` imports under `std::io` must share one use tree ``）

同一段内两个以上叶子共享同一 `prefix`，但源码里没有一条 use 语句正好包含这一组叶子。

```rust
// BAD
use std::io::Read;
use std::io::Write;

// GOOD
use std::io::{Read, Write};
```

**可 fix**（按前缀分桶，每桶渲染成一条 brace 树）。

### USE_PARSE_ERROR — 无法解析的 use 语句

> message 三种：`invalid scoped use list` ／ `invalid use alias` ／ `unsupported use node {type}`

`scoped_use_list` 缺 `path` 或 `list` 字段；`use_as_clause` 具名字节点数 ≠ 2；use 树节点类型不在
`{identifier, crate, self, super, scoped_identifier, use_wildcard, use_as_clause, use_list, scoped_use_list}` 内。

**不可 fix**——段内一旦出现解析错误，该段的整块重写被跳过（`render_block` 在 `USE_PARSE_ERROR` 存在时不执行）。

---

## 规则优先级 / 架构要点（了解即可）

- 项顺序、分组、测试模块结构在**原始源码**上分析（`production_source()` 会掩码 `#[cfg(test)]` 声明，
  恰恰会把要排序的声明藏起来）。其余 `use` 分析在掩码后的生产源码上进行。
- 结构修复器是唯一的结构修复者：先收集作用域内全部 `mod` + `use`，渲染成规范标头（mod 在前、use 在后），
  再重建到作用域头部并删除原声明位置；重建前校验每条声明都出现在标头里，缺失则拒绝修改（绝不删除声明）。
  `check_mod_grouping` 只报告不修改，间距问题由结构修复器的一次性重建顺带解决。

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `traits` | 完整路径列表。命中的 trait 导入必须 `as _`（除非名字被显式引用） | `anyhow::Context`、`futures::FutureExt`、`futures::StreamExt`、`itertools::Itertools`、`serde::Deserialize`、`serde::Serialize`、`std::convert::TryFrom`、`std::convert::TryInto`、`std::io::BufRead`、`std::io::Read`、`std::io::Write`、`std::iter::Iterator`、`tokio_stream::StreamExt`、`tracing::Instrument` |

`traits` 表替换 `defaults.toml` 的 `[use-style]` 段时是整段覆盖（不是增量）。
