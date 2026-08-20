# trait-use-anonymous

> 只用于方法解析的 trait 导入必须写成 `as _`（匿名导入）。
> 代码：`TRAIT001` ｜ `--fix`：不支持（仅检测）

## 目标

一个 trait 被 `use` 引入作用域，但它的名字在文件中从未被显式引用——只通过方法调用语法
（`value.method()`）被编译器解析——那么这个导入是"只用于方法解析"的，应该写成匿名导入
`as _`，因为名字不需要在作用域里，只需要 impl 在。

本规则以目标代码已经通过 `cargo fmt` 和 `cargo clippy -D warnings` 为前提。Clippy 已排除
未使用的普通导入，所以 linter 不需要自行解析依赖源码来证明导入项是 trait。

## 触发条件（全部满足才报）

`TRAIT001` 只在以下条件**同时**成立时触发：

1. 该 `use` 声明是**私有**的（没有 `pub` / `pub(...)`）。`pub use` 从不检查。
2. 导入叶子不以 `::self` 结尾。
3. 导入**不是** `as _`（还没写成匿名）。
4. 导入的本地名是 Rust 类型命名形式（去掉前导 `_` 后以大写字母开头）。在
   `cargo clippy -D warnings` 前提下 trait 必须符合该命名，而小写函数/模块不应被当作 trait。
5. 导入的本地标识符在排除所有 `use` 声明后的整个文件中**从未出现**。以下任一种情况都算
   "显式使用"，不报：
   - 文件包含 `macro_markers` 配置所指定的真实宏调用；该文件的导入可能由宏展开消费，整体豁免；
   - 文件中出现 `macro_invocation`，其文本按整词匹配到该名字；
   - 文件中出现 `identifier` / `type_identifier` 节点（不在任何 `use` 声明内）精确等于该名字——
     覆盖 trait bound（`<T: NamedTrait>`）、`impl NamedTrait for X`、`dyn NamedTrait`、
     限定路径调用（`NamedTrait::method()`）等。

## 违规（BAD）

```rust
mod traits;
use crate::traits::MethodTrait;          // TRAIT001
use crate::traits::NamedTrait;           // OK — 下面被显式使用
use poprako_util::time::ToUnixMilli;     // TRAIT001
struct Value;
impl NamedTrait for Value { fn named(&self) {} }
fn call(value: &Value) { value.ping(); }
fn millis(value: &Value) { value.to_unix_milli(); }
fn bound<T: NamedTrait>() {}
```

- `MethodTrait` 只通过 `value.ping()` 使用方法解析，名字从未出现 → 报。
- `ToUnixMilli` 只通过 `value.to_unix_milli()` 使用，名字从未出现 → 报。
- `NamedTrait` 在 `impl NamedTrait for Value` 和 `fn bound<T: NamedTrait>` 里显式出现 → 不报。

> 原 message：`trait import `{path}` is only used for method resolution; import it as `_``

## 符合（GOOD）

```rust
mod traits;
use crate::traits::MethodTrait as _;
use crate::traits::NamedTrait;
use poprako_util::time::ToUnixMilli as _;
struct Value;
impl NamedTrait for Value { fn named(&self) {} }
fn call(value: &Value) { value.ping(); }
fn millis(value: &Value) { value.to_unix_milli(); }
fn bound<T: NamedTrait>() {}
```

## 别名（`use Foo as Bar`）怎么处理

`use Foo as Bar` 和 `use Foo` 一样被检查：`Bar` 作为本地名参与显式使用检测。
如果 `Bar` 引入后从未被显式使用，同样报 `TRAIT001`，建议改成 `as _`。

## 配置

配置键来自 `[trait-use-anonymous]` 段：

| 键 | 说明 | 默认 |
| --- | --- | --- |
| `macro_markers` | 宏调用标记；文件中存在匹配的 `macro_invocation` 时，视为可能由宏展开消费导入 | `[]` |

> 注：`macro_markers` 是文件级豁免。它只匹配 tree-sitter 解析出的真实宏调用，不会因注释、字符串或
> `macro_rules!` 定义中出现相同文本而触发。
