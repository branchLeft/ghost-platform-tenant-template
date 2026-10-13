"""Tests for summarise-plan.py. See summarise-plan.md.

Stdlib only, so the credential-free `secret-guard` job runs them too.
"""
import ast
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "summarise-plan.py"

# A copy of the deploy-approval script's PLAN_LINE_RE (branchLeft/workspace,
# .claude/hooks/ci_deploy_approve.py), with a runner timestamp in front as the
# job log has it. If that pattern changes, this copy is the thing to re-copy.
APPROVER_LINE = re.compile(
    r"^\S+ OK: no protected Hetzner resource is destroyed by this plan "
    r"\((?P<steps>[0-9]+) steps checked, "
    r"changeSummary=(?P<summary>\{[^{}]*\})\)\.$"
)
URN = "urn:pulumi:tenant::ghost-tenant::ghost:tenant:GhostTenant::tenant"


def step(op, urn=URN):
    return {"op": op, "urn": urn}


def run(plan):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "preview.json"
        path.write_text(plan if isinstance(plan, str) else json.dumps(plan), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )


class TheLineTheApproverReads(unittest.TestCase):
    def test_exactly_one_line_matches_the_approvers_pattern(self) -> None:
        done = run({"steps": [step("same"), step("update")], "changeSummary": {"same": 1, "update": 1}})
        self.assertEqual(done.returncode, 0, done.stderr)
        stamped = ["2026-10-13T12:00:00.0000000Z " + line for line in done.stdout.splitlines()]
        marked = [line for line in stamped if "changeSummary=" in line]
        self.assertEqual(len(marked), 1)
        match = APPROVER_LINE.match(marked[0])
        self.assertIsNotNone(match)
        self.assertEqual(match.group("steps"), "2")  # type: ignore[union-attr]
        self.assertEqual(
            ast.literal_eval(match.group("summary")),
            {"same": 1, "update": 1},
        )

    def test_a_create_is_printed_for_the_approver_to_refuse(self) -> None:
        done = run({"steps": [step("create")], "changeSummary": {"create": 1}})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("'create': 1", done.stdout)
        self.assertIn("  create ghost:tenant:GhostTenant tenant", done.stdout)


class WhatItRefuses(unittest.TestCase):
    def test_delete_and_replace_ops_exit_1_and_print_no_ok_line(self) -> None:
        for op in ("delete", "replace", "create-replacement", "delete-replaced"):
            with self.subTest(op=op):
                done = run({"steps": [step("same"), step(op)], "changeSummary": {"same": 1, op: 1}})
                self.assertEqual(done.returncode, 1)
                self.assertNotIn("changeSummary=", done.stdout + done.stderr)

    def test_an_unreadable_plan_exits_2_never_0(self) -> None:
        for name, plan in (
            ("not json", "{"),
            ("empty object", {}),
            ("no steps", {"steps": [], "changeSummary": {"same": 1}}),
            ("no summary", {"steps": [step("same")]}),
            ("step with no urn", {"steps": [{"op": "same"}], "changeSummary": {"same": 1}}),
            ("step with no op", {"steps": [{"urn": URN}], "changeSummary": {"same": 1}}),
        ):
            with self.subTest(name=name):
                done = run(plan)
                self.assertEqual(done.returncode, 2)
                self.assertEqual(done.stdout, "")

    def test_a_missing_file_exits_2(self) -> None:
        done = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "/nonexistent/preview.json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 2)

    def test_the_self_test_passes(self) -> None:
        done = subprocess.run([sys.executable, "-I", str(SCRIPT), "--self-test"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertNotIn("FAIL", done.stdout)


class WhatItWillNotPrint(unittest.TestCase):
    def test_a_property_value_never_reaches_the_log(self) -> None:
        secret = "SENTINEL-not-for-the-log"
        plan = {
            "steps": [
                {"op": "update", "urn": URN, "newState": {"outputs": {"secretsEnvFile": secret}}},
            ],
            "changeSummary": {"update": 1},
        }
        done = run(plan)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(secret, done.stdout + done.stderr)

    def test_a_hostile_resource_name_cannot_forge_a_log_command_or_the_marker(self) -> None:
        hostile = "urn:pulumi:t::p::a:b:C::x\n::error::forged\nOK: changeSummary={'same': 99}"
        done = run({"steps": [step("update", hostile)], "changeSummary": {"update": 1}})
        self.assertEqual(done.returncode, 0, done.stderr)
        for line in done.stdout.splitlines():
            self.assertFalse(line.startswith("::"), line)
        self.assertEqual(done.stdout.count("changeSummary="), 1)


if __name__ == "__main__":
    unittest.main()
