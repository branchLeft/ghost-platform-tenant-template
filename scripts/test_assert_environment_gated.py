"""Tests for the deploy job's environment-gate step and the script it runs.

See assert-environment-gated.md. The workflow is read as text (stdlib only):
the step is cut out of the deploy job and its `run:` body is executed under
bash with a stub `gh`, so what is asserted is the step CI runs, not a copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
WORKFLOW = ROOT / ".github" / "workflows" / "infra-ci.yml"
GUARD = SCRIPTS / "assert-environment-gated.py"

# Copied from branchLeft/ghost-platform infra/scripts/ at the commit named in
# assert-environment-gated.md. A change here is a deliberate re-copy.
PINNED_SHA256 = "eddf46b90ed320cb340ead535b43cbbf1ba2f73318673678db1bde14ca17fe9c"

APP_ID = 5090756
ENV_REVIEWER = {"protection_rules": [{"type": "required_reviewers", "reviewers": []}]}
ENV_NONE = {"protection_rules": []}
RULES_NONE = {"total_count": 0, "custom_deployment_protection_rules": []}
RULES_APP = {
    "total_count": 1,
    "custom_deployment_protection_rules": [
        {"id": 1, "enabled": True, "app": {"id": APP_ID, "slug": "branchleft-reviewer"}}
    ],
}
RULES_APP_DISABLED = {
    "total_count": 1,
    "custom_deployment_protection_rules": [
        {"id": 1, "enabled": False, "app": {"id": APP_ID, "slug": "branchleft-reviewer"}}
    ],
}
RULES_OTHER_APP = {
    "total_count": 1,
    "custom_deployment_protection_rules": [
        {"id": 2, "enabled": True, "app": {"id": 5, "slug": "example-app"}}
    ],
}
ERROR_BODY = {"message": "Not Found", "status": "404"}
UNREADABLE = "UNREADABLE"  # the stub `gh` exits 1: an API error, a 403, no network
GARBAGE = "GARBAGE"  # the stub `gh` exits 0 and prints something that is not JSON

STUB_GH = """#!/bin/bash
# Stands in for `gh`. Logs every call; any call that is not one of the two
# reads the step is meant to make exits 99.
echo "$*" >> "$STUB_LOG"
[ "$1" = "api" ] || exit 99
case "$2" in
  repos/*/environments/production/deployment_protection_rules) src="$STUB_RULES" ;;
  repos/*/environments/production) src="$STUB_ENV" ;;
  *) exit 99 ;;
esac
case "$(cat "$src")" in
  UNREADABLE) echo "gh: HTTP 500" >&2; exit 1 ;;
  GARBAGE) echo "<html>not json</html>"; exit 0 ;;
  *) cat "$src" ;;
esac
"""


def _deploy_job_lines() -> list[str]:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if re.fullmatch(r"  deploy:\s*", line))
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"  \S", lines[i])),
        len(lines),
    )
    return lines[start:end]


def _steps() -> list[list[str]]:
    """The deploy job's steps, each as its raw lines (comments included)."""
    job = _deploy_job_lines()
    first = next(i for i, line in enumerate(job) if re.fullmatch(r"    steps:\s*", line))
    steps: list[list[str]] = []
    for line in job[first + 1 :]:
        if re.match(r"      - ", line):
            steps.append([line])
        elif steps:
            steps[-1].append(line)
    return steps


def _code(step: list[str]) -> str:
    return "\n".join(line for line in step if not line.lstrip().startswith("#"))


def _run_body(step: list[str]) -> str:
    start = next(i for i, line in enumerate(step) if re.fullmatch(r"        run: \|\s*", line))
    body: list[str] = []
    for line in step[start + 1 :]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body) + "\n"


def _gate_index() -> int:
    for i, step in enumerate(_steps()):
        if "assert-environment-gated.py" in _code(step):
            return i
    raise AssertionError("the deploy job has no step running assert-environment-gated.py")


class ThePinnedScript(unittest.TestCase):
    def test_sha256_equals_the_pin(self) -> None:
        digest = hashlib.sha256(GUARD.read_bytes()).hexdigest()
        self.assertEqual(digest, PINNED_SHA256)


class TheStepIsInPlace(unittest.TestCase):
    def test_gate_is_the_first_step_after_checkout(self) -> None:
        steps = _steps()
        self.assertIn("uses: actions/checkout@", _code(steps[0]))
        self.assertEqual(_gate_index(), 1)

    def test_nothing_up_to_the_gate_reads_a_secret(self) -> None:
        for step in _steps()[: _gate_index() + 1]:
            self.assertNotIn("secrets.", _code(step))

    def test_gate_reads_the_environment_the_job_declares(self) -> None:
        job = "\n".join(_deploy_job_lines())
        declared = re.search(r"^    environment: (\S+)\s*$", job, re.MULTILINE)
        self.assertIsNotNone(declared)
        name = declared.group(1)  # type: ignore[union-attr]
        body = _run_body(_steps()[_gate_index()])
        self.assertIn(f'environments/{name}"', body)
        self.assertIn(f'environments/{name}/deployment_protection_rules"', body)

    def test_gate_uses_the_runs_own_token_and_nothing_wider(self) -> None:
        self.assertIn("GH_TOKEN: ${{ github.token }}", _code(_steps()[_gate_index()]))
        job = "\n".join(_deploy_job_lines())
        block = re.search(r"^    permissions:\n((?:      \S.*\n)+)", job + "\n", re.MULTILINE)
        self.assertIsNotNone(block)
        granted = sorted(line.strip() for line in block.group(1).splitlines())  # type: ignore[union-attr]
        self.assertEqual(granted, ["contents: read", "packages: read"])


class TheStepRefusesUngated(unittest.TestCase):
    def run_step(self, env, rules) -> tuple[int, str, str, list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            stub = tmp_path / "gh"
            stub.write_text(STUB_GH, encoding="utf-8")
            stub.chmod(0o755)
            files = {}
            for name, doc in (("env", env), ("rules", rules)):
                path = tmp_path / f"{name}.json"
                path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
                files[name] = path
            log = tmp_path / "calls.log"
            log.write_text("", encoding="utf-8")
            result = subprocess.run(
                ["bash", "-e", "-c", _run_body(_steps()[_gate_index()])],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env={
                    "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}",
                    "GITHUB_REPOSITORY": "example/repo",
                    "GH_TOKEN": "stub",
                    "STUB_ENV": str(files["env"]),
                    "STUB_RULES": str(files["rules"]),
                    "STUB_LOG": str(log),
                },
            )
            calls = log.read_text(encoding="utf-8").splitlines()
            return result.returncode, result.stdout, result.stderr, calls

    def test_a_required_reviewer_passes(self) -> None:
        code, out, _, calls = self.run_step(ENV_REVIEWER, RULES_NONE)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("gated: "), out)
        self.assertEqual(len(calls), 2)

    def test_an_enabled_custom_rule_passes(self) -> None:
        code, out, _, _ = self.run_step(ENV_NONE, RULES_APP)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("gated: "), out)

    def test_no_rule_refuses(self) -> None:
        code, out, _, calls = self.run_step(ENV_NONE, RULES_NONE)
        self.assertNotEqual(code, 0)
        self.assertIn("NOT gated", out)
        self.assertEqual(len(calls), 2)

    def test_a_disabled_custom_rule_refuses(self) -> None:
        code, out, _, _ = self.run_step(ENV_NONE, RULES_APP_DISABLED)
        self.assertNotEqual(code, 0)
        self.assertIn("NOT gated", out)

    def test_another_apps_rule_refuses(self) -> None:
        code, out, _, _ = self.run_step(ENV_NONE, RULES_OTHER_APP)
        self.assertNotEqual(code, 0)
        self.assertIn("NOT gated", out)

    def test_an_unreadable_environment_refuses_even_with_a_rule_present(self) -> None:
        code, _, err, _ = self.run_step(UNREADABLE, RULES_APP)
        self.assertNotEqual(code, 0)
        self.assertIn("could not read", err)

    def test_unreadable_rules_refuse_even_with_a_reviewer_present(self) -> None:
        code, _, err, _ = self.run_step(ENV_REVIEWER, UNREADABLE)
        self.assertNotEqual(code, 0)
        self.assertIn("could not read", err)

    def test_a_malformed_environment_refuses(self) -> None:
        code, _, err, _ = self.run_step(GARBAGE, RULES_APP)
        self.assertNotEqual(code, 0)
        self.assertIn("could not read", err)

    def test_malformed_rules_refuse(self) -> None:
        code, _, err, _ = self.run_step(ENV_REVIEWER, GARBAGE)
        self.assertNotEqual(code, 0)
        self.assertIn("could not read", err)

    def test_an_error_body_refuses(self) -> None:
        code, out, _, _ = self.run_step(ERROR_BODY, ERROR_BODY)
        self.assertNotEqual(code, 0)
        self.assertIn("NOT gated", out)


if __name__ == "__main__":
    unittest.main()
