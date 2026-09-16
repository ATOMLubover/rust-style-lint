from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class AsciiOnlyTest(unittest.TestCase):
    def test_project_paths_and_contents_are_ascii(self) -> None:
        root = Path(__file__).resolve().parent.parent
        listed = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
        )
        failures = []

        for raw_path in sorted(set(listed.split(b"\0")) - {b""}):
            if not raw_path.isascii():
                failures.append(f"Non-ASCII path: {raw_path!r}")

            path = root / raw_path.decode("utf-8")

            if not path.is_file():
                continue

            for number, line in enumerate(path.read_bytes().splitlines(), 1):
                if not line.isascii():
                    failures.append(f"{raw_path!r}:{number}: non-ASCII content")

        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
