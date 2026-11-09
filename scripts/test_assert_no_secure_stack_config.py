#!/usr/bin/env python3
"""Unit tests for assert-no-secure-stack-config.py.

Every test targets a false pass. See assert-no-secure-stack-config.md's "Tests"
section for why, and why `--self-test` is kept alongside this runner. Every
value is an obvious stand-in; no real credential is written here.
"""

import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent / "assert-no-secure-stack-config.py"


def _load_module():
    """Import the script by path: its filename has hyphens."""
    spec = importlib.util.spec_from_file_location("assert_no_secure_stack_config", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_module()


def lines_flagged(text: str) -> list[int]:
    return [number for number, _ in guard.offending_lines(text)]


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True)


class ConfigCatchesASecret(unittest.TestCase):
    def test_a_secure_entry(self):
        self.assertEqual(lines_flagged("config:\n  p:k:\n    secure: FAKE-CIPHERTEXT\n"), [3])

    def test_a_secure_entry_in_quotes_or_a_list(self):
        self.assertEqual(lines_flagged('config:\n  p:k:\n    "secure": FAKE\n'), [3])
        self.assertEqual(lines_flagged("config:\n  p:k:\n    'secure': FAKE\n"), [3])
        self.assertEqual(lines_flagged("config:\n  p:k:\n    - secure: FAKE\n"), [3])

    def test_each_former_secret_key_is_refused_even_in_plain(self):
        for key in sorted(guard.FORMER_SECRET_KEYS):
            with self.subTest(key=key):
                self.assertEqual(lines_flagged(f"config:\n  p:{key}: FAKE-PLAIN\n"), [2])

    def test_a_new_credential_word_is_refused(self):
        for key in ("stripeToken", "dbPasswd", "clientSecret", "apiKey", "signingPrivateKey", "someCredential"):
            with self.subTest(key=key):
                self.assertEqual(lines_flagged(f"config:\n  p:{key}: FAKE\n"), [2])

    def test_a_bom_does_not_hide_line_one(self):
        self.assertEqual(lines_flagged(guard.BOM + "secure: FAKE\n"), [1])


class ConfigDoesNotCryWolf(unittest.TestCase):
    def test_the_non_secret_keys_this_template_reads_pass(self):
        keys = (
            "slug siteUrl uid appHostPrivateIp hostPort hostPortB healthPort imageRef databaseHost "
            "databasePort databaseMaxUserConnections mediaEndpoint mediaRegion zones hostnameVerifiedAt "
            "backupEncryptionRecipient mailHost mailPort mailUser bulkEmailDomain mailDkimSelector "
            "mailCeiling mailEstateCeiling"
        ).split()
        text = "config:\n" + "".join(f"  p:{key}: x\n" for key in keys)
        self.assertEqual(lines_flagged(text), [])

    def test_the_headers_pulumi_writes_pass(self):
        self.assertEqual(lines_flagged("secretsprovider: passphrase\nencryptedkey: FAKE\nencryptionsalt: v1:A=\n"), [])

    def test_comments_and_values_are_not_findings(self):
        self.assertEqual(lines_flagged("# p:databasePassword removed\n#   secure: FAKE\n"), [])
        self.assertEqual(lines_flagged("config:\n  p:note: a secure password token\n"), [])

    def test_a_key_that_merely_contains_secure_is_not_a_secure_entry(self):
        self.assertEqual(lines_flagged("config:\n  p:secured: x\n  p:insecure: y\n"), [])


class NoValueIsEverPrinted(unittest.TestCase):
    def test_the_report_names_the_key_not_the_value(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "Pulumi.tenant.yaml"
            path.write_text("config:\n  p:mailPassword: FAKE-VALUE-ONE\n  p:o:\n    secure: FAKE-VALUE-TWO\n")
            done = run(str(path))
        self.assertEqual(done.returncode, 1)
        self.assertIn("mailPassword", done.stderr)
        self.assertNotIn("FAKE-VALUE", done.stdout + done.stderr)


class StateCatchesASecret(unittest.TestCase):
    def test_a_secret_object_anywhere_in_the_tree(self):
        deep = {"a": [{"b": {"c": {guard.STATE_SECRET_SIG_KEY: "FAKE-SIG", "ciphertext": "FAKE"}}}]}
        self.assertEqual(list(guard.state_findings(deep)), ["$.a[0].b.c"])

    def test_a_clean_state_passes_and_the_salt_is_not_a_secret(self):
        clean = {"deployment": {"secrets_providers": {"type": "passphrase", "state": {"salt": "v1:A="}}}}
        self.assertEqual(list(guard.state_findings(clean)), [])

    def test_the_path_cannot_forge_a_log_command(self):
        hostile = {"::error::x\nfoo": {guard.STATE_SECRET_SIG_KEY: "FAKE"}}
        (where,) = guard.state_findings(hostile)
        self.assertNotIn("\n", where)
        self.assertEqual(where.count("::"), 2)


class Exits(unittest.TestCase):
    def test_a_clean_tree_exits_0_and_a_secure_one_exits_1(self):
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "Pulumi.tenant.yaml").write_text("config:\n  p:slug: example\n")
            self.assertEqual(run("--scan-tree", raw).returncode, 0)
            (root / "Pulumi.tenant.yaml").write_text("config:\n  p:slug: example\n  p:k:\n    secure: FAKE\n")
            self.assertEqual(run("--scan-tree", raw).returncode, 1)

    def test_a_tree_with_no_stack_config_passes(self):
        with tempfile.TemporaryDirectory() as raw:
            self.assertEqual(run("--scan-tree", raw).returncode, 0)

    def test_an_unreadable_file_exits_3_not_0(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "Pulumi.tenant.yaml"
            path.write_bytes(b"\xff\xfe\x00")
            self.assertEqual(run(str(path)).returncode, 3)
            self.assertEqual(run(str(path.with_name("Pulumi.absent.yaml"))).returncode, 3)

    def test_a_state_file_that_is_not_json_exits_3(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "state.json"
            path.write_text("{not json")
            self.assertEqual(run("--state", str(path)).returncode, 3)

    def test_a_state_with_a_secret_exits_1(self):
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "state.json"
            path.write_text(json.dumps({"o": {guard.STATE_SECRET_SIG_KEY: "FAKE"}}))
            self.assertEqual(run("--state", str(path)).returncode, 1)

    def test_the_self_test_passes(self):
        done = run("--self-test")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertNotIn("FAIL", done.stdout)

    def test_the_report_is_quiet_on_a_pass(self):
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            self.assertEqual(guard.check_configs([]), 0)
        self.assertEqual(buffer.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
