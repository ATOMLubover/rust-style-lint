from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rust_style_lint.checkers.generic_where import check


def violations_for(source: str) -> list:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        src = root / "src"
        src.mkdir()
        (src / "fixture.rs").write_text(source)
        return check(root)


class GenericWhereTest(unittest.TestCase):
    def test_repeated_type_parameter_in_impl_is_reported(self) -> None:
        found = violations_for(
            "impl<L> Step<L> for Repo\n"
            "where\n"
            "    L: Level + Send,\n"
            "    L: AtLeast<RepeatableRead>,\n"
            "{}\n"
        )

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].code, "GEN003")
        self.assertEqual(found[0].line, 4)
        self.assertIn("predicate for L is repeated", found[0].message)

    def test_every_repeated_predicate_after_the_first_is_reported(self) -> None:
        found = violations_for(
            "fn run<T>()\n"
            "where\n"
            "    T: Clone,\n"
            "    T: Send,\n"
            "    T: Sync,\n"
            "{}\n"
        )

        self.assertEqual([violation.code for violation in found], ["GEN003", "GEN003"])
        self.assertEqual([violation.line for violation in found], [4, 5])

    def test_all_where_clause_contexts_are_checked(self) -> None:
        found = violations_for(
            "fn function<T>() where T: Copy, T: Send {}\n"
            "struct Struct<T> where T: Copy, T: Send { value: T }\n"
            "enum Enum<T> where T: Copy, T: Send { Value(T) }\n"
            "union Union<T> where T: Copy, T: Send { value: T }\n"
            "trait Trait<T> where T: Copy, T: Send {}\n"
            "type Alias<T> where T: Copy, T: Send = Vec<T>;\n"
            "trait Associated { type Value<T> where T: Copy, T: Send; }\n"
        )

        self.assertEqual(len(found), 7)
        self.assertEqual({violation.code for violation in found}, {"GEN003"})

    def test_lifetime_and_complex_left_sides_are_checked(self) -> None:
        found = violations_for(
            "fn lifetime<'a>() where 'a: 'static, 'a: 'b {}\n"
            "trait Complex<T> where T::Value: Copy, T::Value: Send {}\n"
        )

        self.assertEqual(len(found), 2)
        self.assertIn("predicate for 'a is repeated", found[0].message)
        self.assertIn("predicate for T::Value is repeated", found[1].message)

    def test_merged_bounds_and_distinct_left_sides_are_allowed(self) -> None:
        found = violations_for(
            "fn merged<T>() where T: Copy + Send {}\n"
            "fn distinct<T, U>() where T: Copy, U: Send {}\n"
            "trait Ranked<F> where for<'a> F: Fn(&'a str), F: Send {}\n"
        )

        self.assertEqual(found, [])

    def test_separate_where_clauses_do_not_conflict(self) -> None:
        found = violations_for(
            "fn first<T>() where T: Copy {}\n"
            "fn second<T>() where T: Send {}\n"
        )

        self.assertEqual(found, [])

    def test_test_only_repeated_predicates_are_ignored(self) -> None:
        found = violations_for(
            "#[cfg(test)]\n"
            "mod tests { fn ignored<T>() where T: Copy, T: Send {} }\n"
        )

        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
