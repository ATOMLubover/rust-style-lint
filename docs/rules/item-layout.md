# item-layout

> 强制手写 Rust 源码里的声明与辅助函数顺序。
> 代码：`LAYOUT001`–`LAYOUT004` ｜ `--fix`：不支持（仅检测）

## 目标

- impl 块必须紧跟其 struct 声明。
- 固有 impl 必须先于 trait impl。
- 私有函数必须排在所有公开函数之后。
- 私有函数必须按首次调用顺序排列。

checker 递归进 `impl_item` 的 `body` 和 `mod_item` 的 `body`，每个嵌套容器独立检查。

## LAYOUT001 — impl 必须紧跟 struct 声明

> message：`impl for {struct_name} must immediately follow its struct declaration`

一个 struct 有一个或多个 impl 块，但 struct 与第一个 impl 之间、或任意两个 impl 之间夹着其他命名项。
所有 impl 块必须连续、紧贴 struct 之后。

```rust
// BAD —— const 隔开了 struct 和 impl
pub struct Wrong;
const SEPARATES_WRONG_IMPL: () = ();
impl Default for Wrong { fn default() -> Self { Self } }
impl Wrong { fn create() {} }

// GOOD
pub struct Good;
impl Good { pub fn create() {} fn prepare() {} }
impl Default for Good { fn default() -> Self { Self } }
pub fn run() { prepare(); finish(); }
fn prepare() {}
fn finish() {}
```

## LAYOUT002 — 固有 impl 必须先于 trait impl

> message：`inherent impl for {struct_name} must precede its trait impls`

struct 有多个 impl 块，某个无 trait 字段的固有 `impl` 出现在 trait impl **之后**。
所有固有 impl 必须在所有 trait impl 之前（按源码顺序遍历，见到 trait impl 后任何后续固有 impl 都违规）。

```rust
// BAD —— trait 在前，固有在后
impl Default for Wrong { fn default() -> Self { Self } }
impl Wrong { fn create() {} }

// GOOD —— 固有在前
impl Good { pub fn create() {} fn prepare() {} }
impl Default for Good { fn default() -> Self { Self } }
```

## LAYOUT003 — 私有函数必须排在所有公开函数之后

> message：`private functions must follow all public functions`

容器内（模块、impl 体、任意出现函数的域），有私有函数的字节偏移早于最后一个公开函数。
即公开函数的最大偏移 > 私有函数的最小偏移时触发。`#[cfg(test)]` 函数排除。

```rust
// BAD —— 私有 second 在公开 run 之前
fn second() {}
/// Calls helpers in their required order.
pub fn run() { first(); second(); }
fn first() {}

// GOOD —— 公开在前，私有在后
pub fn run() { prepare(); finish(); }
fn prepare() {}
fn finish() {}
```

## LAYOUT004 — 私有函数按首次调用顺序

> message：`private function {name} must follow first-call order; {earlier_name} is called earlier`

容器内私有函数未按首次调用位置排序。预期顺序：按容器内任何函数对每个私有函数的**首次调用字节位置**排序，
平局按定义字节位置；从未被调用的函数取 `sys.maxsize` 排到末尾。

```rust
// BAD —— second 先定义，但 first 更早被调用
pub fn run() { first(); second(); }
fn second() {}
fn first() {}

// GOOD
pub fn run() { first(); second(); }
fn first() {}
fn finish() {}   // 未被调用 → 排末尾
```

## 单 fixture 全命中（self-test 断言四种 code 全出）

```rust
pub struct Wrong;
const SEPARATES_WRONG_IMPL: () = ();
impl Default for Wrong { fn default() -> Self { Self } }   // LAYOUT001（被 const 隔开）
impl Wrong { fn create() {} }                              // LAYOUT002（固有在 trait 后）
/// Runs the private helpers.
fn second() {}                                             // LAYOUT003（私有无序）
/// Calls helpers in their required order.
pub fn run() { first(); second(); }
/// Runs before the second helper.
fn first() {}                                              // LAYOUT004（second 应先于 first 定义）
```

预期 code 集合：`{LAYOUT001, LAYOUT002, LAYOUT003, LAYOUT004}`。

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `exclude_files` | 相对 root 的路径列表，`==` 精确匹配即整文件跳过 | `[]` |

`defaults.toml` 里没有 `[item-layout]` 段，且 checker 不使用 `merged()`；`exclude_files` 纯由项目级提供。
