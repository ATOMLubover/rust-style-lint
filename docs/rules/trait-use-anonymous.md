# trait-use-anonymous

> 只用于方法解析的 trait 导入必须写成 `as _`（匿名导入）。
> 代码：`TRAIT001` ｜ `--fix`：不支持（仅检测）

## 目标

一个 trait 被 `use` 引入作用域，但它的名字在引入后从未被显式引用——只通过方法调用语法
（`value.method()`）被编译器解析——那么这个导入是"只用于方法解析"的，应该写成匿名导入
`as _`，因为名字不需要在作用域里，只需要 impl 在。

## 触发条件（全部满足才报）

`TRAIT001` 只在以下条件**同时**成立时触发：

1. 该 `use` 声明是**私有**的（没有 `pub` / `pub(...)`）。`pub use` 从不检查。
2. 导入叶子不以 `::self` 结尾。
3. 导入**不是** `as _`（还没写成匿名）。
4. 导入路径被识别为 trait——满足其一：
   - 完整路径精确命中配置 `external_traits` 列表；**或**
   - 路径最后一个段，与项目里任何 `.rs` 文件中声明的 `trait_item` 名字相同。
5. 该 trait 在文件里引入之后，**从未被显式使用**。以下任一种情况都算"显式使用"，不报：
   - trait 完整路径在 `macro_traits` 配置里，且 `macro_markers` 里任一字符串出现在文件内容中；
   - 引入位置之后出现 `macro_invocation`，其文本按整词匹配到该名字；
   - 引入位置之后出现 `identifier` / `type_identifier` 节点（不在任何 `use` 声明内）精确等于该名字——
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
- `ToUnixMilli` 在 `external_traits` 里，只通过 `value.to_unix_milli()` 使用 → 报。
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
| `external_traits` | 外部 crate trait 的完整路径列表，精确命中即视为 trait（项目内解析不到外部 trait，必须靠这里） | `anyhow::Context`、`futures::FutureExt`、`futures::StreamExt`、`itertools::Itertools`、`serde::Deserialize`、`serde::Serialize`、`std::convert::TryFrom`、`std::convert::TryInto`、`std::io::BufRead`、`std::io::Read`、`std::io::Write`、`std::iter::Iterator`、`tokio_stream::StreamExt`、`tracing::Instrument` |
| `macro_traits` | 完全通过宏消费的 trait 路径列表。路径命中且 `macro_markers` 出现即视为已使用 | `[]` |
| `macro_markers` | 文件级标记字符串，任一出现在文件字节中即视为"该宏 trait 已被使用" | `[]` |

> 注：`macro_markers` 是粗粒度启发式——只是字节级子串搜索，不检查标记是否真的调用了该 trait。
