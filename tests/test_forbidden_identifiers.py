from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.checkers.forbidden_identifiers import check


CONFIG = {
    "words": [
        {
            "word": "expression",
            "code": "OWN001",
            "replacement": "expr",
            "contexts": ["function", "type", "const", "parameter", "let"],
        },
    ],
}


class ForbiddenIdentifiersTest(unittest.TestCase):
    def check_sources(self, sources: dict[str, str]) -> list:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            for relative, source in sources.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)

            return check(root, CONFIG)

    def assert_names(self, source: str, expected: list[str]) -> None:
        found = self.check_sources({"src/lib.rs": source})
        self.assertEqual([violation.code for violation in found], ["OWN001"] * len(expected))
        self.assertEqual(
            [violation.message for violation in found],
            [f"'{name}' uses 'expression'; use 'expr' instead" for name in expected],
        )

    def test_local_trait_members_are_checked_at_declarations_only(self) -> None:
        self.assert_names(
            "trait Local {\n"
            "    type Expression;\n"
            "    const EXPRESSION: usize;\n"
            "    fn expression(&self);\n"
            "}\n"
            "impl Local for Adapter {\n"
            "    type Expression = usize;\n"
            "    const EXPRESSION: usize = 1;\n"
            "    fn expression(&self) {}\n"
            "}\n",
            ["Expression", "EXPRESSION", "expression"],
        )

    def test_external_trait_member_names_are_fixed(self) -> None:
        for imports, trait in [
            ("", "external::Trait"),
            ("use external::Trait;\n", "Trait"),
            ("use external::Trait as Contract;\n", "Contract"),
            ("", "external::Trait<T>"),
            ("use external::{Trait as Contract};\n", "Contract<T>"),
        ]:
            with self.subTest(trait=trait, imports=imports):
                self.assert_names(
                    imports
                    + f"impl<T> {trait} for Adapter<T> {{\n"
                    "    type Expression = external::Expression;\n"
                    "    const EXPRESSION: usize = 1;\n"
                    "    fn expression(&self) {}\n"
                    "}\n",
                    [],
                )

    def test_trait_impl_parameters_and_bindings_remain_checked(self) -> None:
        self.assert_names(
            "impl external::Trait for Adapter {\n"
            "    fn expression(&self, expression: usize) {\n"
            "        let expression = expression;\n"
            "        for expression in 0..1 {}\n"
            "    }\n"
            "}\n",
            ["expression", "expression", "expression"],
        )

    def test_associated_constant_initializer_remains_checked(self) -> None:
        self.assert_names(
            "impl external::Trait for Adapter {\n"
            "    const EXPRESSION: usize = { let expression = 1; expression };\n"
            "}\n",
            ["expression"],
        )

    def test_nested_declarations_are_not_inherited_trait_members(self) -> None:
        self.assert_names(
            "impl external::Trait for Adapter {\n"
            "    fn expression(&self) {\n"
            "        fn expression() {}\n"
            "        type Expression = usize;\n"
            "        const EXPRESSION: usize = 1;\n"
            "        struct Nested;\n"
            "        impl Nested { fn expression(&self) {} }\n"
            "        trait NestedTrait { type Expression; fn expression(&self); }\n"
            "    }\n"
            "}\n",
            ["expression", "Expression", "EXPRESSION", "expression", "Expression", "expression"],
        )

    def test_inherent_members_and_free_definitions_remain_checked(self) -> None:
        self.assert_names(
            "impl Adapter {\n"
            "    const EXPRESSION: usize = 1;\n"
            "    fn expression(&self) {}\n"
            "}\n"
            "fn expression() {}\n"
            "type Expression = usize;\n",
            ["EXPRESSION", "expression", "expression", "Expression"],
        )

    def test_default_trait_members_and_generic_associated_types(self) -> None:
        self.assert_names(
            "trait Local {\n"
            "    type Expression<'a> where Self: 'a;\n"
            "    const EXPRESSION: usize = 1;\n"
            "    fn expression(&self) { let expression = 1; }\n"
            "}\n"
            "impl Local for Adapter {\n"
            "    type Expression<'a> = &'a str;\n"
            "}\n",
            ["Expression", "EXPRESSION", "expression", "expression"],
        )

    def test_external_references_are_not_definitions(self) -> None:
        self.assert_names(
            "use external::expression::{Expression, Trait};\n"
            "fn process(value: Expression) -> external::Expression {\n"
            "    value.expression();\n"
            "    external::expression(value)\n"
            "}\n",
            [],
        )

    def test_trait_declarations_across_modules_and_workspace_members(self) -> None:
        found = self.check_sources({
            "src/lib.rs": "mod contract; mod adapter;\n",
            "src/contract.rs": "pub trait Contract { type Expression; }\n",
            "src/adapter.rs": (
                "use crate::contract::Contract as Local;\n"
                "impl Local for Adapter { type Expression = usize; }\n"
            ),
            "other/src/lib.rs": (
                "trait Local { fn expression(&self); }\n"
                "impl Local for Adapter { fn expression(&self) {} }\n"
                "impl dependency::Contract for Adapter { type Expression = usize; }\n"
            ),
        })
        self.assertEqual(
            {(str(violation.path), violation.line, violation.message) for violation in found},
            {
                ("src/contract.rs", 1, "'Expression' uses 'expression'; use 'expr' instead"),
                ("other/src/lib.rs", 1, "'expression' uses 'expression'; use 'expr' instead"),
            },
        )
        self.assertEqual(len(found), 2)


if __name__ == "__main__":
    unittest.main()
