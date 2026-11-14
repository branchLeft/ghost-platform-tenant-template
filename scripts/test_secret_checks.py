"""Tests for the credential guards in infra-ci.yml's `plan` and `deploy` jobs.
Each guard refuses an empty or whitespace-only value (a stray newline is no use to
Pulumi or ssh), not only an unset one. Steps run as CI runs them, with stubs.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from test_plan_job import _job, _run_text, _step_named  # noqa: E402 -- after the path line above

PHRASE = "empty, whitespace-only or unset"

PASSPHRASE_STEP = "Confirm this stack's secrets passphrase is set"
BACKEND_STEP = "Log in to the Pulumi state backend"
SALT_STEP = "Restore the stack's encryption salt"
DEPLOY_STEP = "Deploy the pinned image to this tenant's slot"

# A stand-in tenant name for the placeholder a template carries and a generated
# repo has already replaced.
SLUG = "blog"

SALT_CONFIG_BODY = "config: {}\n"
KNOWN_HOSTS_BODY = "host.example ssh-ed25519 AAAAstub\n"

# A real key, made by ssh-keygen, so the deploy step's fingerprint line can be
# checked against the fingerprint ssh-keygen itself reports for the public half.
# The comment is a marker: it must never reach the log or the job summary.
KEY_COMMENT = "comment-marker-never-logged"


def _make_deploy_key() -> tuple[str, str]:
    """Return (private key text, the fingerprint ssh-keygen reports for its public half, as the step prints it)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", KEY_COMMENT, "-f", str(path)],
            check=True,
            capture_output=True,
        )
        private = path.read_text(encoding="utf-8")
        report = subprocess.run(
            ["ssh-keygen", "-l", "-f", f"{path}.pub"], check=True, capture_output=True, text=True
        ).stdout.split()
    return private, f"{report[1]} {report[-1]}"


DEPLOY_KEY_BODY, DEPLOY_KEY_FINGERPRINT = _make_deploy_key()
DEPLOY_BASELINE = {
    "DEPLOY_KEY": DEPLOY_KEY_BODY,
    "APP_HOST_SSH_ADDRESS": "host.example",
    "IMAGE_REF": "registry.example/app@sha256:stub",
}

# Stands in for `pulumi`: records its arguments and succeeds.
STUB_PULUMI = """#!/bin/bash
echo "$*" >> "$STUB_PULUMI_LOG"
"""

# Stands in for `ssh`: records its arguments and drains stdin, so the
# `printf | ssh` pipeline under `pipefail` cannot die of a broken pipe.
STUB_SSH = """#!/bin/bash
echo "$*" >> "$STUB_SSH_LOG"
cat > /dev/null
"""

BAD_VALUES = (
    ("empty", ""),
    ("single space", " "),
    ("spaces, tab and newline", " \t\n "),
)


@dataclass(frozen=True)
class Guard:
    """One guarded variable of a step and what its refusal must say and leave undone."""

    variable: str
    names: tuple[str, ...]
    reached_message: str | None


@dataclass(frozen=True)
class RunResult:
    code: int
    output: str
    pulumi_calls: list[str]
    ssh_calls: list[str]
    config_before: str
    config_after: str
    summary: str


@dataclass(frozen=True)
class StepSpec:
    jobs: tuple[str, ...]
    step: str
    baseline: dict[str, str]
    guards: tuple[Guard, ...]
    check_valid: Callable[[unittest.TestCase, RunResult], None]


def _check_passphrase_valid(case: unittest.TestCase, result: RunResult) -> None:
    case.assertEqual(result.output, "")


def _check_backend_valid(case: unittest.TestCase, result: RunResult) -> None:
    case.assertEqual(result.pulumi_calls, ["login s3://example-bucket"])


def _check_salt_valid(case: unittest.TestCase, result: RunResult) -> None:
    case.assertTrue(result.config_after.startswith(result.config_before))
    case.assertIn("encryptionsalt: v1:stub-salt", result.config_after.splitlines())


def _check_deploy_valid(case: unittest.TestCase, result: RunResult) -> None:
    case.assertEqual(len(result.ssh_calls), 1)
    case.assertIn("deploy@host.example", result.ssh_calls[0])


SPECS = (
    StepSpec(
        jobs=("plan", "deploy"),
        step=PASSPHRASE_STEP,
        baseline={"PULUMI_CONFIG_PASSPHRASE": "stub-passphrase"},
        guards=(Guard("PULUMI_CONFIG_PASSPHRASE", ("PULUMI_CONFIG_PASSPHRASE",), None),),
        check_valid=_check_passphrase_valid,
    ),
    StepSpec(
        jobs=("plan", "deploy"),
        step=BACKEND_STEP,
        baseline={
            "PULUMI_BACKEND_URL": "s3://example-bucket",
            "AWS_ACCESS_KEY_ID": "stub-access-key",
            "AWS_SECRET_ACCESS_KEY": "stub-secret-key",
        },
        guards=(
            Guard("PULUMI_BACKEND_URL", ("PULUMI_BACKEND_URL",), "must be an s3://"),
            Guard(
                "AWS_ACCESS_KEY_ID",
                ("HETZNER_S3_ACCESS_KEY_ID", "HETZNER_S3_SECRET_ACCESS_KEY"),
                None,
            ),
            Guard(
                "AWS_SECRET_ACCESS_KEY",
                ("HETZNER_S3_ACCESS_KEY_ID", "HETZNER_S3_SECRET_ACCESS_KEY"),
                None,
            ),
        ),
        check_valid=_check_backend_valid,
    ),
    StepSpec(
        jobs=("plan", "deploy"),
        step=SALT_STEP,
        baseline={"PULUMI_ENCRYPTION_SALT": "v1:stub-salt"},
        guards=(Guard("PULUMI_ENCRYPTION_SALT", ("PULUMI_ENCRYPTION_SALT",), "does not look like"),),
        check_valid=_check_salt_valid,
    ),
    StepSpec(
        jobs=("deploy",),
        step=DEPLOY_STEP,
        baseline=DEPLOY_BASELINE,
        guards=(
            Guard("DEPLOY_KEY", ("APP_HOST_DEPLOY_KEY",), "known_hosts carries no host key line"),
            Guard(
                "APP_HOST_SSH_ADDRESS",
                ("APP_HOST_SSH_ADDRESS",),
                "known_hosts carries no host key line",
            ),
        ),
        check_valid=_check_deploy_valid,
    ),
)


def _step_text(job: str, step: str) -> str:
    return _run_text(_step_named(_job(job), step)).replace("__TENANT_NAME__", SLUG)


def _config_name(text: str) -> str:
    """The stack config file a step names on its own `config=` line, or a neutral name for a step without one."""
    match = re.search(r"^config=(\S+)$", text, re.MULTILINE)
    return match.group(1) if match else "stack-config.yaml"


def _run_step(job: str, step: str, environment: dict[str, str]) -> RunResult:
    text = _step_text(job, step)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        stubs = root / "stubs"
        work.mkdir()
        stubs.mkdir()
        for name, body in (("pulumi", STUB_PULUMI), ("ssh", STUB_SSH)):
            (stubs / name).write_text(body, encoding="utf-8")
            (stubs / name).chmod(0o755)
        script = root / "step.sh"
        script.write_text(text, encoding="utf-8")
        pulumi_log = root / "pulumi.log"
        ssh_log = root / "ssh.log"
        pulumi_log.write_text("", encoding="utf-8")
        ssh_log.write_text("", encoding="utf-8")
        summary = root / "step-summary.md"
        summary.write_text("", encoding="utf-8")
        os.symlink(SCRIPTS, work / "scripts")
        (work / "known_hosts").write_text(KNOWN_HOSTS_BODY, encoding="utf-8")
        config = work / _config_name(text)
        config.write_text(SALT_CONFIG_BODY, encoding="utf-8")
        completed = subprocess.run(
            ["bash", "-e", str(script)],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=60,
            env={
                "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
                "STUB_PULUMI_LOG": str(pulumi_log),
                "STUB_SSH_LOG": str(ssh_log),
                "GITHUB_STEP_SUMMARY": str(summary),
                **environment,
            },
        )
        return RunResult(
            code=completed.returncode,
            output=completed.stdout + completed.stderr,
            pulumi_calls=pulumi_log.read_text(encoding="utf-8").splitlines(),
            ssh_calls=ssh_log.read_text(encoding="utf-8").splitlines(),
            config_before=SALT_CONFIG_BODY,
            config_after=config.read_text(encoding="utf-8"),
            summary=summary.read_text(encoding="utf-8"),
        )


class TheDeployKeyFingerprintIsLoggedBeforeSsh(unittest.TestCase):
    def test_the_fingerprint_is_logged_and_summarised_and_the_key_never_is(self) -> None:
        result = _run_step("deploy", DEPLOY_STEP, dict(DEPLOY_BASELINE))
        self.assertEqual(result.code, 0, result.output)
        printed = result.output + result.summary
        self.assertIn(f"Deploy key fingerprint: {DEPLOY_KEY_FINGERPRINT}", result.output.splitlines())
        self.assertIn(f"Deploy key fingerprint: `{DEPLOY_KEY_FINGERPRINT}`", result.summary.splitlines())
        self.assertNotIn(KEY_COMMENT, printed)
        self.assertNotIn(DEPLOY_KEY_BODY.strip(), printed)
        for body_line in DEPLOY_KEY_BODY.splitlines()[1:-1]:
            self.assertNotIn(body_line, printed)
        self.assertEqual(len(result.ssh_calls), 1)

    def test_a_value_ssh_keygen_cannot_read_is_refused_before_ssh(self) -> None:
        for value_label, value in (("plain text", "stub-deploy-key\n"), ("truncated key", DEPLOY_KEY_BODY[:60])):
            label = f"deploy / {DEPLOY_STEP} / {value_label}"
            with self.subTest(label):
                result = _run_step("deploy", DEPLOY_STEP, {**DEPLOY_BASELINE, "DEPLOY_KEY": value})
                self.assertEqual(result.code, 1, f"{label}\n{result.output}")
                self.assertIn("::error::ssh-keygen could not read APP_HOST_DEPLOY_KEY", result.output, label)
                self.assertEqual(result.ssh_calls, [], label)
                self.assertNotIn("Deploy key fingerprint", result.summary, label)


class TheCredentialGuardsRefuseBlankValues(unittest.TestCase):
    def test_a_blank_value_is_refused_by_name_and_goes_no_further(self) -> None:
        for spec in SPECS:
            for job in spec.jobs:
                for guard in spec.guards:
                    for value_label, value in BAD_VALUES:
                        label = f"{job} / {spec.step} / {guard.variable} / {value_label}"
                        with self.subTest(label):
                            result = _run_step(job, spec.step, {**spec.baseline, guard.variable: value})
                            self.assertEqual(result.code, 1, f"{label}\n{result.output}")
                            self.assertIn(PHRASE, result.output, label)
                            for name in guard.names:
                                self.assertIn(name, result.output, label)
                            self.assertEqual(result.pulumi_calls, [], label)
                            self.assertEqual(result.ssh_calls, [], label)
                            self.assertEqual(result.config_after, result.config_before, label)
                            if guard.reached_message is not None:
                                self.assertNotIn(guard.reached_message, result.output, label)

    def test_a_valid_baseline_passes_and_does_its_work(self) -> None:
        for spec in SPECS:
            for job in spec.jobs:
                label = f"{job} / {spec.step} / valid baseline"
                with self.subTest(label):
                    result = _run_step(job, spec.step, dict(spec.baseline))
                    self.assertEqual(result.code, 0, f"{label}\n{result.output}")
                    self.assertNotIn(PHRASE, result.output, label)
                    spec.check_valid(self, result)


if __name__ == "__main__":
    unittest.main()
