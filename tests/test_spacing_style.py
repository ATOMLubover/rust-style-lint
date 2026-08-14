from __future__ import annotations

import unittest
from pathlib import Path

from rust_style_lint.checkers.spacing_style import RustSpacingChecker, apply_edits


class RustSpacingCheckerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.checker = RustSpacingChecker(Path.cwd())
        self.path = Path("fixture.rs")

    def analyze(self, source: str, *, build_fixes: bool = False):
        return self.checker.analyze_source(
            self.path,
            source.encode(),
            build_fixes=build_fixes,
        )

    def test_cfg_attribute_and_statement_are_one_unit(self) -> None:
        analysis = self.analyze(
            """fn example() {
    //
    let router = router();

    #[cfg(feature = \"swagger-ui\")]
    let router = router.merge(swagger());

    router
}
"""
        )

        self.assertEqual(analysis.diagnostics, ())

    def test_cfg_attribute_on_block_is_one_unit(self) -> None:
        analysis = self.analyze(
            """async fn example() {
    //
    let ctrl_c = ctrl_c();

    #[cfg(unix)]
    {
        terminate().await;
    }

    #[cfg(not(unix))]
    {
        ctrl_c.await;
    }
}
"""
        )

        self.assertEqual(analysis.diagnostics, ())

    def test_fix_does_not_split_attribute_from_statement(self) -> None:
        source = """fn example() {
    //
    let first = 1;
    #[cfg(test)]
    let second = 2;
}
"""
        analysis = self.analyze(source, build_fixes=True)
        fixed = apply_edits(source.encode(), analysis.edits)

        self.assertEqual(
            fixed.decode(),
            """fn example() {
    //
    let first = 1;

    #[cfg(test)]
    let second = 2;
}
""",
        )

    def test_enum_variants_require_a_blank_line(self) -> None:
        source = """enum Payload {
    /// First payload.
    First,
    /// Second payload.
    Second,
}
"""
        analysis = self.analyze(source, build_fixes=True)
        fixed = apply_edits(source.encode(), analysis.edits)

        self.assertEqual(
            [diagnostic.code for diagnostic in analysis.diagnostics],
            ["BLK001"],
        )
        self.assertEqual(
            fixed.decode(),
            """enum Payload {
    /// First payload.
    First,

    /// Second payload.
    Second,
}
""",
        )

    def test_struct_separator_is_forbidden_and_removed(self) -> None:
        source = """struct Payload {
    //
    first: String,
    second: String,
}
"""
        analysis = self.analyze(source, build_fixes=True)
        fixed = apply_edits(source.encode(), analysis.edits)

        self.assertEqual(
            [diagnostic.code for diagnostic in analysis.diagnostics],
            ["BLK002"],
        )
        self.assertEqual(
            fixed.decode(),
            """struct Payload {
    first: String,
    second: String,
}
""",
        )

    def test_struct_literal_separator_is_forbidden_and_removed(self) -> None:
        source = """fn build() {
    //
    let payload = Payload {
        //
        first: String::new(),
        second: String::new(),
    };
}
"""
        analysis = self.analyze(source, build_fixes=True)
        fixed = apply_edits(source.encode(), analysis.edits)

        self.assertEqual(
            [diagnostic.code for diagnostic in analysis.diagnostics],
            ["BLK002"],
        )
        self.assertEqual(
            fixed.decode(),
            """fn build() {
    //
    let payload = Payload {
        first: String::new(),
        second: String::new(),
    };
}
""",
        )

    def test_macro_select_branches_need_separator_and_blank_line(self) -> None:
        analysis = self.analyze(
            """fn run(recv: &mut Receiver, actor: &mut Actor) {
    //
    loop {
        //
        tokio::select! {
            command = recv.recv() => {
                handle();
            }
            task = recv2.recv() => {
                handle2();
            }
        }
    }
}
"""
        )

        self.assertEqual(
            [(diagnostic.code, diagnostic.line) for diagnostic in analysis.diagnostics],
            [("BLK000", 6), ("BLK001", 9)],
        )
        self.assertTrue(
            all(diagnostic.level == "warning" for diagnostic in analysis.diagnostics)
        )

    def test_macro_select_compliant_body_is_clean(self) -> None:
        analysis = self.analyze(
            """fn run(recv: &mut Receiver, actor: &mut Actor) {
    //
    tokio::select! {
        //
        command = recv.recv() => {
            handle();
        }

        task = recv2.recv() => {
            handle2();
        }
    }
}
"""
        )

        self.assertEqual(analysis.diagnostics, ())

    def test_macro_inner_match_arms_need_blank_lines(self) -> None:
        analysis = self.analyze(
            """fn run(recv: &mut Receiver, actor: &mut Actor) {
    //
    tokio::select! {
        //
        command = recv.recv() => {
            match task {
                Some(Ok((tid, ev))) => {
                    actor.task_event(ev);
                }
                None => {}
            }
        }
    }
}
"""
        )

        self.assertEqual(
            [(diagnostic.code, diagnostic.line) for diagnostic in analysis.diagnostics],
            [("BLK000", 6), ("BLK000", 7), ("BLK001", 10)],
        )
        self.assertTrue(
            all(diagnostic.level == "warning" for diagnostic in analysis.diagnostics)
        )

    def test_macro_statement_level_in_arm_body(self) -> None:
        analysis = self.analyze(
            """fn run(recv: &mut Receiver, actor: &mut Actor) {
    //
    tokio::select! {
        //
        command = recv.recv() => {
            let Some(command) = command else {
                actor.shutdown();
                return;
            };

            if !actor.command(command).await {
                return;
            }
        }
    }
}
"""
        )

        self.assertEqual(
            [(diagnostic.code, diagnostic.line) for diagnostic in analysis.diagnostics],
            [("BLK000", 6), ("BLK000", 7), ("BLK001", 8)],
        )

    def test_macro_rules_definition_is_skipped(self) -> None:
        analysis = self.analyze(
            """macro_rules! m {
    ($x:expr) => { 1 };
    ($x:ty) => { 2 };
}

fn f() {
    //
    let v = vec![1, 2, 3];

    let s = format!("x={}", v);
}
"""
        )

        self.assertEqual(analysis.diagnostics, ())

    def test_macro_bodies_never_produce_edits(self) -> None:
        source = """fn run(recv: &mut Receiver, actor: &mut Actor) {
    //
    loop {
        //
        tokio::select! {
            command = recv.recv() => {
                handle();
            }
            task = recv2.recv() => {
                handle2();
            }
        }
    }
}
"""
        analysis = self.analyze(source, build_fixes=True)

        self.assertTrue(any(diagnostic.level == "warning" for diagnostic in analysis.diagnostics))
        self.assertEqual(analysis.edits, ())

    def test_nested_variant_struct_and_following_variant_do_not_overlap(self) -> None:
        source = """enum Payload {
    First {
        //
        first: String,
        second: String,
    },
    Second,
}
"""
        analysis = self.analyze(source, build_fixes=True)
        fixed = apply_edits(source.encode(), analysis.edits)

        self.assertEqual(
            [diagnostic.code for diagnostic in analysis.diagnostics],
            ["BLK002", "BLK001"],
        )
        self.assertEqual(
            fixed.decode(),
            """enum Payload {
    First {
        first: String,
        second: String,
    },

    Second,
}
""",
        )


if __name__ == "__main__":
    unittest.main()
