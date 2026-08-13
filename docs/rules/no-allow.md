# no-allow

> 禁止任何 lint 抑制属性：`#[allow(...)]` / `#[expect(...)]`。
> 代码：`NO_ALLOW` ｜ `--fix`：不支持（仅检测）

## 目标

抑制（`allow` / `expect`）掩盖真实问题，并随着代码演化而腐烂。消除抑制的**唯一**途径是重构代码让 lint 停发：

- 未使用的条件编译导入 → 改成 `#[cfg(feature = "...")] use ...;`（swagger 宏属性里才用的类型）
- `dead_code` → 删除死代码，或把确实要用的项标 `pub`
- 函数参数太多 → 收进 struct / builder
- 等等

非抑制属性不受影响：`#[cfg(...)]`、`#[derive(...)]`、`#[cfg_attr(..., derive(...))]`、`#[deprecated]` 等照常。

## 触发

任意位置出现 `#[allow(...)]` 或 `#[expect(...)]` 即报，message 里带上被抑制的 lint 名。

```rust
// BAD —— 抑制未使用的导入
#[allow(unused_imports)]
use crate::data::val::chapter_port::ExportChapterTranslationVal;

// GOOD —— 只有 swagger 启用时才导入（宏属性里用到，未启用时不存在未使用问题）
#[cfg(feature = "swagger")]
use crate::data::val::chapter_port::ExportChapterTranslationVal;

// BAD —— 抑制死代码
#[allow(dead_code)]
fn helper() {}

// GOOD —— 删掉它，或让调用方真正使用它
```

## 实现

对每个 `.rs` 文件，用 tree-sitter 找出所有 `attribute_item`，正则匹配
`#\[(allow|expect)\(...\)\]`。匹配到即报 `NO_ALLOW`。

**不可 fix**：`--fix` 是 no-op，必须手工重构消除。
