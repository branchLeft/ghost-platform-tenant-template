"""Unit tests for assert-placeholders-substituted.py.

The interesting test is not that the script finds a placeholder in a file it
was handed -- it is `test_default_files_covers_every_placeholder_in_the_tree`,
which is the only thing standing between a future edit and a placeholder that
travels into a generated tenant repo unchecked. `DEFAULT_FILES` is a hand-kept
list, and a file dropped from it stops being covered silently.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO = SCRIPTS.parent

_spec = importlib.util.spec_from_file_location(
    "assert_placeholders_substituted", SCRIPTS / "assert-placeholders-substituted.py"
)
assert _spec and _spec.loader
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)

# Prose names placeholders in order to document them, and this test file and
# the script itself both carry the pattern as data. Substitution happens in
# files a tool reads, so those are what the coverage assertion is about.
EXCLUDED_SUFFIXES = {".md"}
EXCLUDED_DIRS = {"scripts", "graphify-out", ".claude"}

# A `#` comment line naming a placeholder is documenting it, not carrying one:
# `.prettierignore` explains why the markdown files are excluded from
# formatting, and a config comment is not a value anything reads. A placeholder
# in a comment is also substituted harmlessly if the file is substituted at all,
# so a false negative here costs nothing while a false positive would train
# whoever hits it to widen the exclusions instead.
COMMENT_LINE = re.compile(r"^\s*#")


class FindPlaceholders(unittest.TestCase):
    def test_reports_each_distinct_placeholder_once_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "f.yml"
            path.write_text("__B_TWO__ __A_ONE__ __B_TWO__\n", encoding="utf-8")
            self.assertEqual(module.find_placeholders(path), ["__A_ONE__", "__B_TWO__"])

    def test_lowercase_and_single_underscore_forms_are_not_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "f.yml"
            path.write_text("__lower__ _SINGLE_ __1LEADING_DIGIT__\n", encoding="utf-8")
            self.assertEqual(module.find_placeholders(path), [])


class Main(unittest.TestCase):
    def test_clean_files_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "f.yml"
            path.write_text("name: blog-infra\n", encoding="utf-8")
            self.assertEqual(module.main(["prog", str(path)]), 0)

    def test_a_surviving_placeholder_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "f.yml"
            path.write_text("name: __TENANT_PULUMI_PROJECT__\n", encoding="utf-8")
            self.assertEqual(module.main(["prog", str(path)]), 1)

    def test_a_missing_file_exits_one_rather_than_passing(self) -> None:
        # A renamed or moved file must fail closed: "nothing to check" and
        # "checked and clean" are the same exit status otherwise, and the first
        # is how a placeholder reaches a host.
        self.assertEqual(module.main(["prog", "no/such/file.yml"]), 1)


class DefaultFiles(unittest.TestCase):
    def test_every_named_file_exists_in_this_repo(self) -> None:
        for name in module.DEFAULT_FILES:
            self.assertTrue((REPO / name).is_file(), f"DEFAULT_FILES names a missing file: {name}")

    def test_the_template_itself_still_carries_every_placeholder(self) -> None:
        # An unsubstituted placeholder is the correct, permanent state of this
        # repo's `main`. A file that stopped carrying one is a file whose
        # per-tenant value has quietly become a constant.
        for name in module.DEFAULT_FILES:
            self.assertTrue(
                module.find_placeholders(REPO / name),
                f"{name} carries no placeholder; either it no longer needs one "
                "(remove it from DEFAULT_FILES and README.md's table) or a "
                "substitution was committed by mistake.",
            )

    def test_default_files_covers_every_placeholder_in_the_tree(self) -> None:
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO,
            capture_output=True,
            check=True,
        )
        covered = set(module.DEFAULT_FILES)
        uncovered: list[str] = []
        for name in listing.stdout.decode().split("\0"):
            if not name:
                continue
            path = pathlib.Path(name)
            if path.suffix in EXCLUDED_SUFFIXES or path.parts[0] in EXCLUDED_DIRS:
                continue
            if name in covered:
                continue
            try:
                text = (REPO / path).read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            if any(
                module.PLACEHOLDER_PATTERN.search(line)
                for line in text.splitlines()
                if not COMMENT_LINE.match(line)
            ):
                uncovered.append(name)
        self.assertEqual(
            uncovered,
            [],
            "these tracked files carry a __PLACEHOLDER__ that the substitution "
            "check does not cover; add them to DEFAULT_FILES and to README.md's "
            "placeholder table, and make sure the provisioning flow substitutes "
            "them",
        )


if __name__ == "__main__":
    unittest.main()
