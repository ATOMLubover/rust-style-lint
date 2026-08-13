# module-dependency

> 强制 Rust 模块依赖"向下或平级"（downward-or-across），拒绝反向依赖和循环依赖。
> 代码：`MOD001`（向上依赖）、`MOD002`（循环依赖）｜ `--fix`：不支持（仅检测）

## 目标

模块层级是一棵树（根是 crate 根）。一条引用边（一个模块用到另一个模块）可以**向下**（依赖更深的后代）
或**平级**（依赖同级/更低层级的模块），但**禁止严格向上**依赖祖先模块。更低的模块不得依赖它的祖先。

具体来说，给定 `parent` 和 `parent::child`：

- `parent` 用到 `parent::child` 的东西 → 允许（向下）。
- `parent::child::a` 用到 `parent::child::b` 的东西 → 允许（平级）。
- `parent::child` 用到 `parent` 的东西 → **禁止**，报 `MOD001`。

## MOD001 — 向上依赖

> message：`{source} must not depend only upward on strict ancestor {target}; reference: `{reference}``

当模块 A 依赖模块 B，而 B 是 A 在模块树中的**严格祖先**（`len(B) < len(A)` 且 `A[:len(B)] == B`）时触发。
三种边来源都会检查：

1. `use` 声明
2. 非 use 代码里的限定路径（`scoped_identifier` / `scoped_type_identifier`）
3. 外层属性参数里内嵌的路径（如 `#[allow(crate::parent::lint)]`，正则只匹配 `crate::` / `self::` / `super::` 开头）

### 违规（BAD）

`src/parent/child.rs` —— 子模块向上依赖父模块（6 处）：

```rust
use super::Owner as ParentOwner;
pub use super::Owner;
use super::*;
fn alias(_: ParentOwner) {}
fn qualified(_: crate::parent::Owner) {}
#[allow(crate::parent::lint)] struct Attributed;
```

`src/parent/impls.rs` —— 另一种向上依赖：

```rust
use super::Owner;
use crate::port::Trait;
impl Trait for Owner {
    fn use_again(&self) { let _: Option<Owner> = None; }
}
```

- `impl Trait for Owner` 头里的 `Owner` 被豁免（见下方豁免 2），但 `use_again` 函数体里的
  `Option<Owner>` 是第 7 处向上依赖。

### 符合（GOOD）

`src/parent/child.rs` —— 子依赖平级兄弟 `parent::shared`：

```rust
use super::shared::Helper;
pub struct Child;
fn helper(_: Helper) {}
```

`src/parent.rs`：

```rust
pub struct Owner;
pub mod child;
pub mod shared;
pub mod impls;
use self::child::Child;
fn child(_: Child) {}
```

`src/lib.rs`：

```rust
mod parent;
mod port;
mod part_impl;
#[cfg(test)]
mod tests;
```

## MOD002 — 循环依赖

> message：`cyclic module dependency {source} -> {target} in [{cycle members}]`

对所有收集到的依赖边建图，用 Tarjan 强连通分量算法检测。任何包含超过一个模块的 SCC 都是环。
环内每条边（源和目标都属于该 SCC）各报一条 `MOD002`。

### 违规（BAD）

```rust
// src/lib.rs
mod a;
mod b;
mod c;

// src/a.rs
use crate::b::B;
pub struct A(B);

// src/b.rs
use crate::c::C;
pub struct B(C);

// src/c.rs
use crate::a::A;
pub struct C(A);
```

`a -> b -> c -> a`，三条边，报 3 条 `MOD002`。

## MOD001 vs MOD002

| | MOD001 | MOD002 |
| --- | --- | --- |
| 是什么 | 单条向上依赖 | 参与循环 |
| 触发 | 依赖边本身指向严格祖先，无论有没有更大的环 | 边位于多模块 SCC 内 |
| 关系 | — | 一条边既向上又在环里时，会同时报 MOD001 和 MOD002 |

## MOD003 — 重名定义（警告，不失败）

> message：`duplicate {type alias|struct|trait} name `{name}` defined in {modules}`

扫描全部生产模块，收集模块级 `type` 别名、`struct`、`trait` 定义。同一名字在**两个或更多不同模块**
里各定义一次时，每个定义位置各报一条 `MOD003`。比如父模块 `engine` 定义了 `pub type AgentEngineResult`，
子模块 `engine::lifecycle` 又私有定义 `type AgentEngineResult` 覆盖它——这会报两条 `MOD003` 警告。

**`MOD003` 是 `level="warning"`，只打印、不判失败**（runner 只对 `error` 级违规返回非 0）。
它提示命名冲突/遮蔽是个坏味道，但能编译，所以不阻塞。

test 模块（`prefixes` 命中）里的定义不计入。

## 路径如何解析（super / self / crate）

- `crate::` 或 crate 名（从 `Cargo.toml` 的 name 读取）→ 相对 crate 根 `()` 解析。
- `self::` → 相对当前模块。
- `super::` → 相对当前模块的父级；`super::super::` 逐级上弹。
- 顶层模块名（首段命中已知根级模块）→ 视为 `crate::` 前缀。
- 无法识别（外部 crate 名 / 未知标识符）→ 不产生边，直接忽略。

`use` 树展开时：`self` 折叠进当前前缀，`*` 通配符展开，`as` 别名跟踪用于纯 impl 头过滤。

## 模块路径如何计算

**文件路径 → 模块元组**（`file_module`）：

| 文件 | 模块路径 |
| --- | --- |
| `src/lib.rs` / `src/main.rs`（顶层） | `()`（crate 根） |
| `src/foo.rs` | `("foo",)` |
| `src/foo/bar.rs` | `("foo", "bar")` |
| `src/foo/mod.rs` | `("foo",)`（去掉 `mod.rs`） |

**内联模块**：从 AST 节点向上找每个带 `body` 的 `mod_item`，名字前置。

## 豁免（不产生违规的边）

1. **`#[cfg(test)]` 相关代码**：受 `cfg(test)`（或等价条件）保护的条目不收集边。用 `CfgParser` 把 `test` 强制为 `False`，只有所有赋值都为 `False` 才算 test-only。
2. **纯 impl 头引用**：限定路径节点位于 `impl_item` 的 trait / self-type 位置（`impl Trait for MyType`）时豁免。但该豁免**不适用于 `use` 边**——use 边有单独的豁免：`alias_is_pure_impl()` 检查导入别名唯一的使用位置是否全在 impl 头内，是则整条 use 边豁免。
3. **自引用**：`target == source` 的边跳过。
4. **无法解析的路径**：`absolute_path()` 或 `target_module()` 返回 `None` 时不产生边。

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `exclude_files` | 相对 root 的路径列表，命中的文件整文件跳过（不产生边、不会违规） | `[]` |

`defaults.toml` 里没有 `[module-dependency]` 段；项目配置的 `exclude_files` 即全部。
