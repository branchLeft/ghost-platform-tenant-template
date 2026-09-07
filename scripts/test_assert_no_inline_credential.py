"""Unit tests for assert-no-inline-credential.py.

The interesting tests are the two placeholder shapes
(`test_ellipsis_form_is_caught`, matching `test_angle_bracket_form_is_caught`)
and the two must-not-flag controls: a non-credential export
(`test_non_credential_export_is_not_flagged`, the "address half" the owner's
principle deliberately leaves alone) and the safe `read`-based replacement
itself (`test_the_safe_read_pattern_is_not_flagged`) -- a guard that flags its
own remedy would train people to route around it.
"""

from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "assert_no_inline_credential", SCRIPTS / "assert-no-inline-credential.py"
)
assert _spec and _spec.loader
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def _write(tmp: str, text: str) -> pathlib.Path:
    path = pathlib.Path(tmp) / "RUNBOOK-example.md"
    path.write_text(text, encoding="utf-8")
    return path


class IsPlaceholder(unittest.TestCase):
    def test_angle_bracket_form(self) -> None:
        self.assertTrue(module.is_placeholder("<the escrowed value, decrypted>"))

    def test_ellipsis_form(self) -> None:
        self.assertTrue(module.is_placeholder("…"))
        self.assertTrue(module.is_placeholder("..."))

    def test_a_real_looking_value_is_not_a_placeholder(self) -> None:
        self.assertFalse(module.is_placeholder("$STACK_SALT"))
        self.assertFalse(module.is_placeholder("AKIAABCDEXAMPLE1234"))

    def test_empty_value_is_not_a_placeholder(self) -> None:
        self.assertFalse(module.is_placeholder(""))
        self.assertFalse(module.is_placeholder("   "))


class FindOffenses(unittest.TestCase):
    def test_angle_bracket_form_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "```bash\n"
                "export PULUMI_CONFIG_PASSPHRASE='<the escrowed value, decrypted>'\n"
                "```\n",
            )
            offenses = module.find_offenses(path)
            self.assertEqual(len(offenses), 1)
            self.assertIn("PULUMI_CONFIG_PASSPHRASE", offenses[0])

    def test_ellipsis_form_is_caught(self) -> None:
        """The proof this guard is worth more than fixing the instances: the
        exact ghost-tenant-blog defect, wearing an ellipsis instead of angle
        brackets, which a `<...>`-only guard would not catch.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "export PULUMI_CONFIG_PASSPHRASE='…'\n")
            offenses = module.find_offenses(path)
            self.assertEqual(len(offenses), 1)

    def test_ellipsis_form_with_trailing_comment_is_caught(self) -> None:
        """The literal ghost-tenant-blog instance carries a trailing shell
        comment (`# from the password manager`) after the closing quote --
        an end-of-line anchor that does not tolerate one misses this real
        instance entirely, which is exactly what a first draft of this
        pattern did until this test caught it against the real file.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "export PULUMI_CONFIG_PASSPHRASE='…'          # from the password manager\n",
            )
            offenses = module.find_offenses(path)
            self.assertEqual(len(offenses), 1)

    def test_ascii_ellipsis_form_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "export AWS_SECRET_ACCESS_KEY='...'\n")
            offenses = module.find_offenses(path)
            self.assertEqual(len(offenses), 1)

    def test_printf_salt_placeholder_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "printf '\\nencryptionsalt: %s\\n' '<this stack salt>' >> Pulumi.x.yaml\n",
            )
            offenses = module.find_offenses(path)
            self.assertEqual(len(offenses), 1)

    def test_printf_salt_ellipsis_is_caught(self) -> None:
        """The literal ghost-tenant-blog instance."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp, "printf '\\nencryptionsalt: %s\\n' '…' >> Pulumi.blog.yaml\n"
            )
            offenses = module.find_offenses(path)
            self.assertEqual(len(offenses), 1)

    def test_printf_smtp_password_placeholder_is_caught(self) -> None:
        """The literal website instance: a credential keyword in the format
        string, not in a shell variable name.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp, 'printf "CONTACT_SMTP_PASSWORD=%s\\n" "<SMTP_PASSWORD>";\n'
            )
            offenses = module.find_offenses(path)
            self.assertEqual(len(offenses), 1)

    def test_non_credential_export_is_not_flagged(self) -> None:
        """The address half of the owner's principle is a different, already
        -handled concern -- an unsubstituted address fails loudly on its own,
        and this guard flagging it too would just be noise.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "export TENANT_SLUG='<slug>'\n")
            self.assertEqual(module.find_offenses(path), [])

    def test_the_safe_read_pattern_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(
                tmp,
                "printf 'PULUMI_CONFIG_PASSPHRASE (the escrowed value, decrypted): '; "
                "read -rs PULUMI_CONFIG_PASSPHRASE; echo; export PULUMI_CONFIG_PASSPHRASE\n"
                'printf \'\\nencryptionsalt: %s\\n\' "$STACK_SALT" >> Pulumi.x.yaml\n',
            )
            self.assertEqual(module.find_offenses(path), [])

    def test_a_clean_file_has_no_offenses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "# Just some prose about <a placeholder> in text.\n")
            self.assertEqual(module.find_offenses(path), [])


class Main(unittest.TestCase):
    def test_a_missing_file_exits_one(self) -> None:
        self.assertEqual(module.main(["prog", "/no/such/file.md"]), 1)

    def test_no_files_given_exits_two(self) -> None:
        self.assertEqual(module.main(["prog"]), 2)

    def test_clean_file_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "nothing to see here\n")
            self.assertEqual(module.main(["prog", str(path)]), 0)

    def test_offending_file_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "export API_TOKEN='<value>'\n")
            self.assertEqual(module.main(["prog", str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
