# prefer-if-let-guard

> 单个业务分支的 match 应该改写成 `if` / `if let` / `let ... else`。
> 代码：`LET001` ｜ `--fix`：不支持（仅检测，建议手动改写）

## 目标

一个 match 应该表达多个业务分支。当恰好一个分支做实际工作、其余分支都只是退场
（`return`、`break`、`continue`、发散宏、或空体）时，这个 match 其实是单路径分发，
用 guard 读起来更清楚。

```rust
match value {
    Some(x) => foo(x),
    None => return,
}
```

改写成：

```rust
let Some(x) = value else {
    return;
};

foo(x);
```

如果恰好只有两个 arm，且一个 arm 发散、另一个 arm 为空，也应改成 `if let`：

```rust
match value {
    Some(x) => {
        return foo(x);
    }
    None => {}
}

if let Some(x) = value {
    return foo(x);
}
```

guard 全是空体时改成 `if let`：

```rust
match value {
    Some(x) => foo(x),
    None => {}
}
```

改写成：

```rust
if let Some(x) = value {
    foo(x);
}
```

布尔字面量的两臂 match 不使用 `if let true/false`，直接改成普通条件：

```rust
match created {
    true => {},
    false => update(),
}

if !created {
    update();
}
```

## LET001 — 单业务分支 match

> message（发散 guard）：`match has a single business arm `{pattern}` and a diverging guard; prefer `let {pattern} = {scrutinee} else {{ ... }}` over a match whose only other arms bail out`
> message（空 guard）：`match has a single business arm `{pattern}` and only empty guard arms; prefer `if let {pattern} = {scrutinee} {{ ... }}``
> message（发散臂 + 空臂）：`match has a diverging arm `{pattern}` and an empty opposite arm; prefer `if let {pattern} = {scrutinee} {{ ... }}``
> message（布尔分支）：`boolean match has a single business arm `{pattern}` and an empty opposite arm; prefer `if {condition} {{ ... }}``
> 例：`match has a single business arm `Some(x)` and a diverging guard; prefer `let Some(x) = value else { ... }` over a match whose only other arms bail out`

### 臂分类

每个 match arm 归为四类之一：

| 分类 | 判定 |
| --- | --- |
| `guarded` | 无 pattern、pattern 带 `condition`（match guard）、或无 value |
| `empty` | 空 `block`、或 `unit_expression`（`()`） |
| `diverging` | 尾部表达式发散——`return_expression` / `break_expression` / `continue_expression`，或名字在 `diverging_macros` 里的宏调用。**只看尾部表达式**：块里前部有个 `if`/`for`/`loop`/`match` 里夹着 return 不算发散（`is_diverging_block`），因为前置分支可能不执行 |
| `business` | 其他（做实际工作） |

### 触发条件（全部满足）

1. 臂数 ≥ 2，且没有 `guarded` 臂。
2. match 满足以下任一形态：
   - 恰好 1 个 `business` 臂，且至少 1 个 guard 臂；
   - 恰好 2 个臂，且一个是 `empty`、另一个是 `diverging`。
3. 对第一种形态，任一 guard 臂的体都没有引用其 pattern 引入的绑定（`guard_uses_pattern_binding`）。
   `let ... else` 的 else 块无法命名匹配失败的值——比如 `Err(err) => return f(err)` 没有干净的 let-else 形式，跳过。
   检测法：pattern 里小写开头的 `identifier` 视为绑定（`None`/`Err` 这类单元变体是大写，不算），
   若体引用了其中任意一个，跳过。
4. 要转换的臂 pattern 不是裸 `_`。
5. 按 guard 分类决定改写方向：
   - guard 全为 `empty`，且恰好是 `true` / `false` 两臂 → `if condition`；业务臂为 `false` 时取反。
   - 其他 guard 全为 `empty` → `if-let`。
   - guard 全为 `diverging` **且**所有 guard 体的文本完全相同 → `let-else`（能合并进同一个 else 块）。
   - 恰好两个臂且混合 `empty` + `diverging` → 以发散臂为目标改成 `if-let`。
   - 多于两个臂且混合 `empty` + `diverging` → 不触发。
   - guard 发散方式不同（如一个 `return`、一个 `panic!`）→ 不触发。

### 违规（BAD）

```rust
// let-else 建议
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => return,            // 发散 guard
    }
}

// if-let 建议
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => {},                // 空 guard
    }
}

// 单元表达式 guard 也按空算
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        _ => (),
    }
}

// 尾部发散块（前有语句也算发散）
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => {
            warn("missing");
            return;
        },
    }
}

// 发散宏 guard（默认表含 panic/unreachable/todo/unimplemented）
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => unreachable!(),
    }
}

// 发散臂 + 空臂 → if-let
pub fn f(member_info: Option<MemberInfo>) {
    match member_info {
        Some(member_info) => {
            return accept(UnitListAccessInfo::Member(member_info));
        }
        None => {}
    }
}

// 多 guard 但体相同 → 可合并进一个 else
pub fn f(value: Option<u8>) {
    match value {
        Some(x) => foo(x),
        None => return,
        _ => return,
    }
}

// guard 只绑 `_` 不引用绑定 → 可改写
pub fn f(value: Result<u8, String>) -> u8 {
    match value {
        Ok(x) => x,
        Err(_) => return 0,
    }
}
```

### 符合（GOOD）——不触发的场景

```rust
// 两个业务臂
match value {
    Some(x) => foo(x),
    None => bar(),
}

// 多 pattern 分发
match value {
    Some(x) => a(x),
    Some(y) => b(y),
    None => return,
}

// 通配符业务兜底（业务臂是 _）
match value {
    Some(x) => foo(x),
    _ => default(),
}

// match guard 无法用 if-let 表达
match value {
    Some(x) if x > 5 => foo(x),
    None => return,
}

// guard 体引用了自身 pattern 的绑定
match value {
    Ok(x) => x,
    Err(err) => return err.len() as u8,
}

// 多于两个臂的混合空 + 发散 guard
match value {
    Some(x) => foo(x),
    None => {},
    _ => return,
}

// guard 发散方式不同
match value {
    Some(x) => foo(x),
    None => return,
    _ => panic!("impossible"),
}

// guard 块的尾部不发散（前置 if 夹着 return 不算）
match value {
    Some(x) => foo(x),
    None => {
        if x {
            return;
        }
    },
}

// 单臂 match
match value {
    Some(x) => foo(x),
}
```

## 配置

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `diverging_macros` | 视为发散宏的名字列表（不含 `::` 前缀，比较 `macro_name` 的最后一段） | `["panic", "unreachable", "todo", "unimplemented"]` |
| `exclude_segments` | 路径任一成分命中即跳过整个文件 | `[]` |
| `exclude_filename_prefixes` | 路径任一成分以某前缀开头即跳过 | `[]` |
| `exclude_filenames` | 精确文件名匹配即跳过 | `[]` |
