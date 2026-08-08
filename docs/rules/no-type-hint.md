# no-type-hint

> 禁止 `let` 绑定上的类型注解提示；用推断或 turbofish 代替。
> 代码：`NO_TYPE_HINT` ｜ `--fix`：**no-op**（只报告，不编辑）

## 目标

规则统一：**任何** `let x: T = value` 都不允许。当值的类型无法推断时，把类型钉在值的泛型调用上
（turbofish），而不是注解绑定。

```rust
let x = expr.collect::<Vec<_>>();       // GOOD
let y = resolver.parse::<u32>()?;       // GOOD — turbofish 在调用上
let z: u32 = expr.parse()?;             // BAD — let 绑定上的类型提示
```

## NO_TYPE_HINT — let 绑定类型注解

> message：`type hint `{type_ann}` on let binding; remove the annotation and rely on inference, or pin the type with turbofish on the value's generic call`
> `{type_ann}` 是类型注解节点的完整文本（如 `u32`、`HashMap<String, i32>`、`Vec<&str>`）。

### 触发条件

AST 里出现 `let_declaration`，且 `child_by_field_name("type")` 非空（即绑定带任何类型注解）。

**没有任何例外**——无论类型复杂度、宏展开、任何上下文，`let x: T = ...` 一律触发。

### 替代写法

| 风格 | 判定 | 示例 |
| --- | --- | --- |
| `let` 上类型注解 | BAD → NO_TYPE_HINT | `let z: u32 = expr.parse()?;` |
| 调用上 turbofish | GOOD | `let y = resolver.parse::<u32>()?;` |
| 纯推断 | GOOD | `let x = expr.collect::<Vec<_>>();` |

## 为什么 --fix 是 no-op

`fix_file()` 存在但明确不动源码：

> Removing an annotation can silently change the inferred type (literals, `as` casts,
> generic constructors) or break compilation entirely (Diesel `.first()?` / `.load()`),
> and turbofish is not valid on every method. Auto-editing is therefore a no-op:
> the check reports, and the developer applies the recommended fix by hand.

`--fix` 接受该 flag（API 兼容）但向 stderr 打印：

```
note: --fix is a no-op; remove or turbofish the annotation by hand
(auto-editing can change the inferred type or break compilation)
```

## 配置

**无配置键**。`defaults.toml` 里没有 `[no-type-hint]` 段。使用 `production_source()` 掩码测试代码后解析，
但 checker 本身没有可配置行为。
