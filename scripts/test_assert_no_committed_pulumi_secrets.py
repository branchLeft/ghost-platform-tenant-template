#!/usr/bin/env python3
"""Unit tests for assert-no-committed-pulumi-secrets.

The guard has no second line of defence: a salt it clears is a salt that
reaches a tree anyone can clone, and no later gate looks for one. So the
failure shape every test below is aimed at is a *false pass* -- a key the
matcher does not recognise, a file the walker does not visit, a path it cannot
read and treats as clean, or an exit status that says nothing was found when
nothing was looked at.

The script's own `--self-test` covers the same matcher from the inside, and
both are kept: CI and the pre-commit hook invoke the script rather than this
runner, and a check whose only verification lives in a second tool is one that
stops being verified the first time the two are wired differently.
"""

import contextlib
import importlib.util
import io
import pathlib
import tempfile
import unittest


def _load_module():
    """Import the script by path: its filename has hyphens, so it is not a
    legal module name for a plain import."""
    path = pathlib.Path(__file__).resolve().parent / "assert-no-committed-pulumi-secrets.py"
    spec = importlib.util.spec_from_file_location("assert_no_committed_pulumi_secrets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_module()


def lines_flagged(text: str) -> list[int]:
    return [number for number, _ in guard.offending_lines(text)]


class MatcherCatchesASalt(unittest.TestCase):
    """Every shape Pulumi writes, and the shapes an edit could turn it into."""

    def test_plain_key_at_column_zero(self):
        self.assertEqual(lines_flagged("config:\n  a: b\nencryptionsalt: v1:AAA=\n"), [3])

    def test_indented_key(self):
        self.assertEqual(lines_flagged("config:\n  encryptionsalt: v1:AAA=\n"), [2])

    def test_key_as_a_list_item(self):
        self.assertEqual(lines_flagged("config:\n  l:\n    - encryptionsalt: AAAA\n"), [3])

    def test_case_is_ignored(self):
        # The bytes are an oracle whatever case wraps the key, even where
        # Pulumi's own parser would skip the line.
        for key in ("EncryptionSalt", "ENCRYPTIONSALT", "eNcRyPtIoNsAlT"):
            with self.subTest(key=key):
                self.assertEqual(lines_flagged(f"{key}: v1:AAA=\n"), [1])

    def test_quoted_keys(self):
        # Pulumi never writes one, but every YAML parser reads these as the
        # same key, so a file carrying one decrypts exactly as before.
        self.assertEqual(lines_flagged('"encryptionsalt": v1:AAA=\n'), [1])
        self.assertEqual(lines_flagged("'encryptionsalt': v1:AAA=\n"), [1])
        self.assertEqual(lines_flagged('  "encryptionsalt" : v1:AAA=\n'), [1])

    def test_hash_inside_the_value_is_not_a_comment(self):
        self.assertEqual(lines_flagged("encryptionsalt: v1:AAA=#notacomment\n"), [1])

    def test_leading_byte_order_mark_does_not_hide_line_one(self):
        self.assertEqual(lines_flagged(guard.BOM + "encryptionsalt: v1:AAA=\n"), [1])

    def test_a_salt_beside_a_ciphertext_is_still_caught(self):
        text = "config:\n  p:t:\n    secure: AAAA\nencryptionsalt: v1:AAA=\n"
        self.assertEqual(lines_flagged(text), [4])

    def test_every_offending_line_is_reported_not_just_the_first(self):
        text = "encryptionsalt: v1:AAA=\nconfig:\n  encryptionsalt: v1:BBB=\n"
        self.assertEqual(lines_flagged(text), [1, 3])


class MatcherDoesNotCryWolf(unittest.TestCase):
    """A guard people start bypassing protects nothing, so the false-positive
    side is asserted as deliberately as the false-negative side."""

    def test_a_ciphertext_with_no_salt_is_allowed(self):
        # branchLeft/standards PUL-12: a `secure:` value with no salt beside it
        # is not an oracle, and banning it would ban the safe half of the
        # pattern. Widening the matcher here would also put this guard and the
        # standards gate into disagreement.
        self.assertEqual(lines_flagged("config:\n  p:t:\n    secure: AAAABBBB\n"), [])

    def test_a_key_merely_containing_the_word(self):
        self.assertEqual(lines_flagged("config:\n  p:noencryptionsalt: x\n"), [])
        self.assertEqual(lines_flagged("config:\n  p:encryptionsaltish: y\n"), [])
        self.assertEqual(lines_flagged('config:\n  "p:noencryptionsalt": x\n'), [])

    def test_a_commented_line(self):
        text = "# encryptionsalt: v1:AAA=\n  #  encryptionsalt: v1:BBB=\nconfig:\n  a: b\n"
        self.assertEqual(lines_flagged(text), [])

    def test_the_word_appearing_in_a_value(self):
        self.assertEqual(lines_flagged("config:\n  p:note: encryptionsalt: none\n"), [])

    def test_a_clean_config(self):
        self.assertEqual(lines_flagged("config:\n  gcp:project: p\n  p:region: r\n"), [])


class FileSelection(unittest.TestCase):
    def test_stack_configs_are_matched_and_project_files_are_not(self):
        for name, expected in [
            ("Pulumi.tenant.yaml", True),
            ("Pulumi.blog.yaml", True),
            ("Pulumi.yaml", False),
            ("Pulumi.tenant.yaml.bak", False),
            ("Pulumi.tenant.yml", False),
            ("something.yaml", False),
        ]:
            with self.subTest(name=name):
                self.assertIs(guard.is_stack_config(pathlib.Path("a/b") / name), expected)

    def test_scan_tree_recurses_and_skips_vendored_trees(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "Pulumi.tenant.yaml").write_text("config: {}\n", encoding="utf-8")
            (root / "Pulumi.yaml").write_text("name: x\n", encoding="utf-8")
            for skipped in guard.SKIP_DIRS:
                (root / skipped).mkdir()
                (root / skipped / "Pulumi.fixture.yaml").write_text(
                    "encryptionsalt: v1:AAA=\n", encoding="utf-8"
                )
            found = {p.relative_to(root).as_posix() for p in guard.find_stack_configs(root)}
            self.assertEqual(found, {"Pulumi.tenant.yaml"})


class ExitStatus(unittest.TestCase):
    """`check` and `main` are what CI reads; the matcher above is not."""

    @staticmethod
    def _run(argv):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = guard.main(argv)
        return code, buffer.getvalue()

    def test_a_salted_file_exits_one_and_names_the_file(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "Pulumi.tenant.yaml"
            path.write_text("encryptionsalt: v1:AAA=\n", encoding="utf-8")
            code, report = self._run([str(path)])
            self.assertEqual(code, 1)
            self.assertIn("Pulumi.tenant.yaml", report)

    def test_a_clean_file_exits_zero(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "Pulumi.tenant.yaml"
            path.write_text("config:\n  gcp:project: p\n", encoding="utf-8")
            self.assertEqual(self._run([str(path)])[0], 0)

    def test_an_unreadable_path_fails_rather_than_reporting_clean(self):
        with tempfile.TemporaryDirectory() as raw:
            missing = pathlib.Path(raw) / "Pulumi.missing.yaml"
            self.assertEqual(self._run([str(missing)])[0], 1)

    def test_scan_tree_over_a_tree_with_no_stack_config_exits_zero(self):
        # This repo's own shape. `--scan-tree` matching nothing is a clean
        # tree, not a usage error -- treating it as one would fail CI on every
        # run here and in a tenant whose stack config has not landed yet.
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "Pulumi.yaml").write_text("name: x\nruntime: nodejs\n", encoding="utf-8")
            self.assertEqual(self._run(["--scan-tree", str(root)])[0], 0)

    def test_scan_tree_finds_a_salt_nobody_named(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "Pulumi.tenant.yaml").write_text("encryptionsalt: v1:AAA=\n", encoding="utf-8")
            self.assertEqual(self._run(["--scan-tree", str(root)])[0], 1)

    def test_the_bundled_self_test_passes(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(guard.main(["--self-test"]), 0)


if __name__ == "__main__":
    unittest.main()
