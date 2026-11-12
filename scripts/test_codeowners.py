"""Tests for .github/CODEOWNERS: which paths the owner's team holds.

The file is read with GitHub's own rules (the last matching pattern wins, a
leading slash anchors, a slash-less pattern matches at any depth, a trailing
slash is a directory tree, a pattern with no owner releases what an earlier line
took), and a fixed table of sample paths says whether each is owned. The table
has at least one path for every class the file keeps and one for every class it
releases, so a change that drops an owner, or hands one away, turns a named
sample red.

`CODEOWNERS_FILE` points the tests at another file, which is how the checks are
shown red against the previous file and against a deliberately broken copy.
`python3 scripts/test_codeowners.py --count` prints how many tracked files the
file owns; it reads `git ls-files`, which the tests themselves never do.

Stdlib only, so the credential-free `secret-guard` job runs it too. The shared
SYNC-1 lines are not re-implemented here: the repository's Standards check
reads the real file for those.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent
DEFAULT_FILE = REPO / ".github" / "CODEOWNERS"
TEAM = "@branchLeft/branchleft-admin"

# Patterns that would own the whole tree. None may carry an owner.
CATCH_ALLS = ("*", "/*", "**", "/**", "**/*", "/**/*")


class Rule(NamedTuple):
    pattern: str
    owners: tuple[str, ...]


def parse(text: str) -> list[Rule]:
    """Read CODEOWNERS text into rules, in file order.

    Anything GitHub's format does not support (negation, character ranges,
    escapes) is refused rather than guessed at.
    """
    rules: list[Rule] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens: list[str] = []
        for token in line.split():
            if token.startswith("#"):
                break
            tokens.append(token)
        pattern, owners = tokens[0], tuple(tokens[1:])
        if pattern.startswith("!") or "[" in pattern or "\\" in pattern:
            raise ValueError(f"line {number}: unsupported pattern {pattern!r}")
        rules.append(Rule(pattern, owners))
    return rules


def _glob_to_regex(glob: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(glob):
        char = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("/**", i) and i + 3 == len(glob):
            out.append("/.*")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return "".join(out)


def matches(pattern: str, path: str) -> bool:
    """Whether `pattern` covers `path`, the way GitHub reads CODEOWNERS."""
    directory_only = pattern.endswith("/")
    body = pattern.rstrip("/")
    anchored = body.startswith("/") or "/" in body
    body = body.lstrip("/")
    regex = re.compile(("" if anchored else "(?:.*/)?") + _glob_to_regex(body))
    parts = path.split("/")
    for depth in range(1, len(parts) + 1):
        is_directory = depth < len(parts)
        if directory_only and not is_directory:
            continue
        if regex.fullmatch("/".join(parts[:depth])):
            return True
    return False


def owners_of(rules: Sequence[Rule], path: str) -> tuple[str, ...]:
    """The owners of `path`: those of the last rule that matches, else none."""
    for rule in reversed(rules):
        if matches(rule.pattern, path):
            return rule.owners
    return ()


def load_rules() -> list[Rule]:
    chosen = os.environ.get("CODEOWNERS_FILE")
    path = Path(chosen) if chosen else DEFAULT_FILE
    return parse(path.read_text(encoding="utf-8"))


# Paths the owner's team must hold, by the class that keeps them.
OWNED: dict[str, tuple[str, ...]] = {
    "1 the rules themselves": (
        ".github/CODEOWNERS",
        "CLAUDE.md",
        "docs/CLAUDE.md",
        "AUTHORISATIONS.md",
        ".claude/settings.json",
        "tools/floors.tsv",
        ".standardsignore",
        ".standards.mode",
        ".docs-lint.yml",
        ".standards-db-tooling",
    ),
    "1 and 2 scripts and tests the secret-bearing jobs depend on": (
        "scripts/image-changed.py",
        "scripts/summarise-plan.py",
        "scripts/assert-placeholders-substituted.py",
        "scripts/assert-plan-environment-main-only.py",
        "scripts/assert-no-committed-pulumi-secrets.py",
        "scripts/assert-environment-gated.py",
        "scripts/test_image_changed.py",
        "scripts/test_summarise_plan.py",
        "scripts/test_plan_job.py",
        "scripts/test_secret_checks.py",
        "scripts/test_generate_pnpm_lockfile.py",
        "scripts/test_codeowners.py",
        "scripts/testdata/image-changed/runs-empty.json",
        # The required 'Committed-secret guard' check: plan and deploy need it,
        # so its exit code decides whether a deploy happens.
        "scripts/assert-no-inline-credential.py",
        "scripts/test_assert_no_inline_credential.py",
        # Runs in both required checks; guards against a drifted copy failing open.
        "scripts/test_generate_tenant_repo.py",
    ),
    "1 the test of the test:render gate": ("test/render.test.ts",),
    "2 what runs, and what Pulumi runs, where secrets are in scope": (
        "package.json",
        "pnpm-workspace.yaml",
        ".npmrc",
        ".pnpmfile.cjs",
        ".pnpmfile.mjs",
        "known_hosts",
        "Pulumi.yaml",
        "Pulumi.blog.yaml",
        "index.ts",
        "config.ts",
        "tsconfig.json",
    ),
    "3 workflows and actions": (
        ".github/workflows/infra-ci.yml",
        ".github/workflows/standards.yml",
        ".github/actions/example/action.yml",
        ".github/dependabot.yml",
        # Under /.github/, which the shared lines force to be the last: owned
        # although it is a document.
        ".github/workflows/generate-pnpm-lockfile.md",
    ),
    "8 the owner's words": ("LICENSE",),
}

# Paths no one is asked to review: documents, the generator that no workflow
# runs, and lockfiles and version pins (the deeper agent review takes those).
RELEASED: dict[str, tuple[str, ...]] = {
    "documents": (
        "README.md",
        "README.tenant.md",
        "RUNBOOK-bootstrap.md",
        "config.md",
        "index.md",
        "scripts/image-changed.md",
        "scripts/assert-no-inline-credential.md",
    ),
    "the generator, which no workflow runs": ("scripts/generate-tenant-repo.py",),
    "lockfiles and version pins": ("pnpm-lock.yaml", ".nvmrc"),
    "editor, formatter and hook settings": (
        ".editorconfig",
        ".prettierignore",
        ".gitignore",
        ".pre-commit-config.yaml",
    ),
}


class MatcherRules(unittest.TestCase):
    """The reading of CODEOWNERS, on a file written for the purpose."""

    RULES = parse(
        "\n".join(
            (
                "# a comment",
                "*.md @a",
                "/docs/ @b",
                "/docs/*.txt",
                "/src/**/gen/ @c",
                "NOTES @d # trailing comment",
                "/top.cfg @e",
                "/top.cfg",
                "/f?le @f",
                "/last/ @g",
                "/last/free.txt",
                "",
            )
        )
    )

    def owners(self, path: str) -> tuple[str, ...]:
        return owners_of(self.RULES, path)

    def test_last_matching_pattern_wins(self) -> None:
        self.assertEqual(self.owners("docs/readme.md"), ("@b",))

    def test_a_slash_less_pattern_matches_at_any_depth(self) -> None:
        self.assertEqual(self.owners("a/b/c.md"), ("@a",))
        self.assertEqual(self.owners("c.md"), ("@a",))
        self.assertEqual(self.owners("a/NOTES"), ("@d",))

    def test_a_leading_slash_anchors_to_the_root(self) -> None:
        self.assertEqual(self.owners("deep/top.cfg"), ())
        self.assertEqual(self.owners("deep/f1le"), ())

    def test_a_pattern_with_no_owner_releases(self) -> None:
        self.assertEqual(self.owners("top.cfg"), ())
        self.assertEqual(self.owners("docs/a.txt"), ())
        self.assertEqual(self.owners("last/free.txt"), ())

    def test_a_trailing_slash_is_the_whole_directory_tree(self) -> None:
        self.assertEqual(self.owners("last/sub/deep.bin"), ("@g",))
        self.assertEqual(self.owners("docs/a/b/c.bin"), ("@b",))

    def test_a_star_does_not_cross_a_slash(self) -> None:
        self.assertEqual(self.owners("docs/sub/a.txt"), ("@b",))

    def test_a_double_star_crosses_directories(self) -> None:
        self.assertEqual(self.owners("src/x/y/gen/out.ts"), ("@c",))
        self.assertEqual(self.owners("src/gen/out.ts"), ("@c",))

    def test_a_question_mark_is_one_character(self) -> None:
        self.assertEqual(self.owners("file"), ("@f",))
        self.assertEqual(self.owners("fle"), ())

    def test_a_path_matching_nothing_has_no_owner(self) -> None:
        self.assertEqual(self.owners("elsewhere/x.bin"), ())

    def test_unsupported_syntax_is_refused(self) -> None:
        for line in ("!/x @a", "/[ab] @a", "\\#x @a"):
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse(line)


class CodeownersFile(unittest.TestCase):
    """The file under test, against the fixed sample table."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_rules()

    def test_owned_samples_are_owned_by_the_team(self) -> None:
        for kept, paths in OWNED.items():
            for path in paths:
                with self.subTest(kept=kept, path=path):
                    self.assertEqual(owners_of(self.rules, path), (TEAM,))

    def test_released_samples_have_no_owner(self) -> None:
        for released, paths in RELEASED.items():
            for path in paths:
                with self.subTest(released=released, path=path):
                    self.assertEqual(owners_of(self.rules, path), ())

    def test_no_owned_catch_all(self) -> None:
        offending = [r.pattern for r in self.rules if r.pattern in CATCH_ALLS and r.owners]
        self.assertEqual(offending, [])

    def test_every_owned_line_names_the_team_and_nobody_else(self) -> None:
        for rule in self.rules:
            if rule.owners:
                with self.subTest(pattern=rule.pattern):
                    self.assertEqual(rule.owners, (TEAM,))


def count_owned() -> tuple[int, int]:
    """How many tracked files the file owns, and how many are tracked."""
    listing = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    rules = load_rules()
    owned = sum(1 for path in listing if owners_of(rules, path))
    return owned, len(listing)


if __name__ == "__main__":
    if sys.argv[1:] == ["--count"]:
        owned_files, tracked = count_owned()
        print(f"owned {owned_files} of {tracked} tracked files")
    else:
        unittest.main()
