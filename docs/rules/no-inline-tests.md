# no-inline-tests

> 禁止内联的 `#[cfg(test)] mod tests { ... }` —— 测试必须抽到独立的 `tests.rs` 文件。
> 代码：`TST001` ｜ `--fix`：不支持（仅检测）

## 目标

测试模块必须以**文件式声明**存在：

```rust
#[cfg(test)]
mod tests;
```

分号结尾、无 `body`，测试代码放在独立的 `src/tests.rs`（或子模块的 `src/<parent>/tests.rs`）
或 `src/tests/mod.rs` 里。任何花括号内联形式的 `#[cfg(test)] mod tests { ... }` 都是违规。

## 触发条件（全部满足才报）

1. AST 节点是 `mod_item`。
2. 模块 `name` 字段**精确等于** `"tests"`。（叫别的名字不查。）
3. 模块有 `body` 字段——即花括号内联形式，**不是**分号文件式声明。
4. `has_test_only_cfg` 返回真——模块（或任意父级）带有 `#[cfg(test)]` 或等价属性。
   `CfgParser` 把 `test` 强制为 `False`，只有所有可能赋值都为 `False` 才判定为 test-only。
   能正确处理 `#[cfg(all(test, feature = "x"))]`、`#[cfg(any(test))]`、`#[cfg(not(not(test)))]` 等复杂形式。

任意嵌套层级都会查，不限顶层。

## 违规（BAD）

```rust
// src/things.rs
pub fn do_thing() {}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn it_works() {}
}
```

以下两个变体同样报 `TST001`：

```rust
#[cfg(test)]
// This comment belongs to the test module.
mod tests {   // 注释在 cfg 和 mod 之间不豁免
    use super::*;
}
```

```rust
// src/lib.rs 自身内联测试
pub mod things;
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn it_works() {}
}
```

> message：`inline #[cfg(test)] mod tests { ... } is forbidden; extract tests into a separate tests.rs file`

## 符合（GOOD）

```rust
// src/lib.rs
pub mod things;
#[cfg(test)] mod tests;
```

```rust
// src/things.rs
pub fn do_thing() {}
#[cfg(test)]
// Tests remain in a sibling file.
mod tests;
```

分号的 `mod tests;` 没有 body，不触发。

## 配置

此 checker **不读取任何配置键**。`defaults.toml` 里没有对应段。
