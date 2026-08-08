# generic-where

> 要求泛型类型/生命周期约束必须写在 `where` 子句里；禁止参数位置的 `impl Trait`。
> 代码：`GEN001`（内联约束）、`GEN002`（参数位置 impl Trait）｜ `--fix`：不支持（仅检测）

## 目标

- 泛型参数上的约束（`T: Copy`、`'a: 'static`）必须移到 `where` 子句。
- 参数位置的 `impl Trait` 一律禁止——引入具名泛型参数，把约束放进 `where` 子句。
  返回值位置的 `impl Trait`（`-> impl Trait`）**允许**。

## GEN001 — 泛型参数内联约束

> message：`generic parameter {parameter_name} in {declaration_name} uses an inline bound; move the bound to a where clause`

### 触发条件

以下声明类型之一带泛型参数（`type_parameter` 或 `lifetime_parameter`），且参数带 `bounds` 字段（语法含 `: Bound`）：
`enum_item`、`function_item`、`impl_item`、`struct_item`、`trait_item`、`type_item`、`union_item`。
类型参数（`T: Copy`）和生命周期参数（`'a: 'static`）都查。
`impl` 项没有 name 字段，message 里 `{declaration_name}` 用 `"impl"`。

### 违规（BAD）—— 9 处

```rust
fn bad_fn<T: Copy, 'a: 'static>() {}    // 2 处：T: Copy、'a: 'static
impl<T: Copy> Item<T> {}                // 1 处
struct BadStruct<T: Copy> {}            // 1 处
enum BadEnum<T: Copy> {}                // 1 处
trait BadTrait<T: Copy> {}              // 1 处
type BadAlias<T: Copy> = Vec<T>;        // 1 处
union BadUnion<T: Copy> { value: T }    // 1 处
```

`#[cfg(any(test, feature = "extra"))]` 包裹的 `mod maybe_production { fn bad_mod<T: Copy>() {} }`
**不豁免**——`test` 强制为 false 后 `any(...)` 仍可能为真（`feature = "extra"` 可达），因此计入生产代码。

### 符合（GOOD）

```rust
fn clean<T>() {}
fn constrained<T>() where T: Copy {}
struct Item<T> where T: Copy {}
impl<T> Item<T> where T: Copy {}
fn return_opaque() -> impl Iterator<Item = u8> { todo!() }
#[cfg(test)]
mod tests { fn ignored<T: Copy>() {} }   // 测试代码被掩码，不查
```

## GEN002 — 参数位置的 `impl Trait`

> message：`inline impl Trait is forbidden; introduce a named generic parameter and move the bound to a where clause`

### 触发条件

tree-sitter 的 `abstract_type` 节点（即 `impl Trait` 语法）**不在返回值位置**。
返回值位置通过向上穿过 `bounded_type` 父节点链、检查节点是否是父声明的 `return_type` 字段来判断。
参数位置、`let` 绑定、类型别名、以及其他任何非返回上下文都触发。
`impl Trait` 包在引用里（`&(impl EffectDevelop + Sync)`）仍能检测到——无论嵌套多深都会穿过 `bounded_type` 链。

### 违规（BAD）—— 2 处

```rust
fn bad_impl_trait(develop: &(impl EffectDevelop + Sync), other: impl Other) {}
//                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^
//                 GEN002 #1                             GEN002 #2
```

### 符合（GOOD）—— 返回值位置允许

```rust
fn return_opaque() -> impl Iterator<Item = u8> { todo!() }
```

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `exclude_files` | 相对 root 的路径列表，精确命中即整文件跳过 | `[]` |

`defaults.toml` 里没有 `[generic-where]` 段，且 checker 不使用 `merged()`；`config=None` 时不排除任何文件。
