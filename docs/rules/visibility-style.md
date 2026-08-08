# visibility-style

> 强制纯 Rust 可见性（只有 `pub` 和私有）与私有实现字段。
> 代码：`VIS001`（受限可见性）、`VIS002`（非白名单模块的 pub 字段）｜ `--fix`：不支持（仅检测）

## 目标

生产代码只允许两种可见性：

- **纯 `pub`**：无括号限制的 `pub` 关键字。
- **私有**：完全没有可见性修饰符（限定在模块内）。

其他一切——`pub(crate)`、`pub(super)`、`pub(self)`、`pub(in path)`——都是"受限可见性"，由 `VIS001` 禁止。
struct 字段默认可变则必须私有，只有显式白名单模块里允许公开字段。

## VIS001 — 受限可见性

> message：`restricted visibility `{visibility}` is forbidden; production Rust permits only plain `pub` or private items; share an internal item with plain `pub` behind a private module`
> 如 `restricted visibility 'pub(crate)' is forbidden; ...`

### 触发条件

DFS 遍历遇到 `visibility_modifier` 节点，且：

1. 所在模块未被排除——模块 `mod` 声明带 `#[cfg(test)]`（或 `CfgParser` 判定 `test=false` 时永远为假的条件）；
   或项本身带 `has_test_only_cfg()` 为真的属性（沿祖先链向上查）。
2. 可见性修饰符规范化文本**不是**精确的 `"pub"`。

## VIS002 — 非白名单模块的公开 struct 字段

> message：`public struct field is forbidden in {formatted_module}; {rule}; expose construction or access through functions`
> `{formatted_module}`：空模块路径为 `crate`，否则 `crate::` + `::` 连接段。
> `{rule}`：有白名单时 `plain-public struct fields are allowed only in crate::model, crate::data; fields elsewhere must be private`；空白名单时 `... in no module; ...`

### 触发条件

遇到可见性修饰符节点，且：

1. 模块未被排除（同 VIS001）。
2. 该可见性属于 **struct 字段**——父节点是 `field_declaration` 且祖父的父是 `struct_item`（具名字段），
   或父节点是 `ordered_field_declaration_list` 且祖父是 `struct_item`（元组 struct 字段）。
3. 当前模块不在 `allow_public_fields` 白名单里（模块路径不以任何白名单前缀开头）。

### VIS001 和 VIS002 可以同时触发

两者是**独立**的 if。`pub(crate)` 字段在非白名单模块里会同时报 VIS001（不是 `pub`）和 VIS002（是字段且不在白名单）。

### 违规（BAD）—— `src/service.rs`，6 VIS001 + 4 VIS002

```rust
pub(crate) struct Restricted;            // VIS001
pub(super) fn parent_only() {}           // VIS001
pub(self) const LOCAL: i32 = 1;         // VIS001
pub(in crate::service) type Scoped = i32; // VIS001
pub struct Named { pub value: i32, pub(crate) other: i32 }
//                        VIS002          VIS001 + VIS002
pub struct Tuple(pub i32, pub(super) i32);
//              VIS002   VIS001 + VIS002
#[cfg(test)] pub(crate) fn ignored() {}              // 跳过
#[cfg(all(test, feature = "rdb"))] pub struct TestOnly { pub value: i32 }  // 跳过
```

### 符合（GOOD）

```rust
// src/model.rs —— 白名单，允许公开字段
pub struct Model { pub value: i32 }

// src/data.rs —— 白名单
pub struct Data(pub i32);

// src/service.rs —— 非白名单：字段私有，可见性只允许 pub 或私有
pub struct Service { value: i32 }
pub struct Tuple(i32);
```

## CfgParser 与 test-only 判定

checker 内置小型 cfg 表达式求值器：`#[cfg(...)]` 表达式在**强制 `test=false`** 下求所有可能真值。
若表达式在 `test=false` 时永远不可能为真，项就是 test-only，跳过所有可见性规则。
`has_test_only_cfg()` 沿祖先链上溯，父 `mod` 声明带 `#[cfg(test)]` 会传播到模块内所有项。

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `allow_public_fields` | 允许公开 struct 字段的模块路径列表，`::` 分隔、相对 crate 根（如 `"model"`、`"part::repo::oper"`） | `[]` |
| `exclude_files` | 相对 root 的文件路径，整文件跳过扫描 | 无（defaults.toml 未定义） |
