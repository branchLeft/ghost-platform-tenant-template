"""Tests for the plan job in infra-ci.yml and the branch-policy guard it runs.

See summarise-plan.md and assert-plan-environment-main-only.md. The workflow is
read as text (stdlib only), the way test_assert_environment_gated.py reads the
deploy job, so the credential-free `secret-guard` job runs these too.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
WORKFLOW = SCRIPTS.parent / ".github" / "workflows" / "infra-ci.yml"
MAIN_ONLY = SCRIPTS / "assert-plan-environment-main-only.py"

# The steps the plan job repeats from the deploy job. A plan made with a
# different login, salt, preview or guard than the apply is a plan of something
# else, so these must be equal to the character.
SHARED_STEPS = (
    "Confirm this stack's secrets passphrase is set",
    "Log in to the Pulumi state backend",
    "Restore the stack's encryption salt",
    "Preview the plan",
    "Refuse to apply a plan carrying a delete or replace",
)
STATE_SECRETS = {
    "PULUMI_CONFIG_PASSPHRASE",
    "PULUMI_ENCRYPTION_SALT",
    "HETZNER_S3_ACCESS_KEY_ID",
    "HETZNER_S3_SECRET_ACCESS_KEY",
}


def _job(name: str) -> list[str]:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if re.fullmatch(rf"  {name}:\s*", line))
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"  \S", lines[i])), len(lines))
    return lines[start:end]


def _steps(job: list[str]) -> list[list[str]]:
    first = next(i for i, line in enumerate(job) if re.fullmatch(r"    steps:\s*", line))
    steps: list[list[str]] = []
    for line in job[first + 1 :]:
        if re.match(r"      - ", line):
            steps.append([line])
        elif steps:
            steps[-1].append(line)
    return steps


def _code(lines: list[str]) -> str:
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _step_named(job: list[str], name: str) -> list[str]:
    for step in _steps(job):
        if re.fullmatch(rf"      - name: {re.escape(name)}\s*", step[0]):
            return step
    raise AssertionError(f"no step named {name!r}")


def _names(job: list[str]) -> list[str]:
    return [re.sub(r"^      - name: ", "", step[0]).strip() for step in _steps(job)]


def _field(job: list[str], key: str) -> str:
    found = [line for line in job if re.match(rf"    {key}: ", line)]
    assert len(found) == 1, (key, found)
    return found[0].split(": ", 1)[1].strip()


class ThePlanJobIsWiredAheadOfTheGate(unittest.TestCase):
    def test_the_deploy_job_waits_for_the_plan_job(self) -> None:
        needs = _field(_job("deploy"), "needs")
        self.assertRegex(needs, r"\bplan\b")
        self.assertRegex(needs, r"\btypecheck\b")
        self.assertRegex(needs, r"\bsecret-guard\b")

    def test_the_plan_job_has_exactly_the_deploy_jobs_condition(self) -> None:
        self.assertEqual(_field(_job("plan"), "if"), _field(_job("deploy"), "if"))

    def test_the_plan_job_never_runs_in_the_template_or_off_main(self) -> None:
        condition = _field(_job("plan"), "if")
        self.assertIn("github.ref == 'refs/heads/main'", condition)
        self.assertIn("github.event_name == 'push'", condition)
        self.assertIn("github.event.repository.is_template != true", condition)

    def test_the_gate_is_still_on_the_deploy_job_alone(self) -> None:
        self.assertEqual(_field(_job("deploy"), "environment"), "production")
        self.assertEqual(_field(_job("plan"), "environment"), "production-plan")
        self.assertNotIn("environment: production\n", _code(_job("plan")) + "\n")

    def test_the_deploy_job_still_previews_and_checks_for_itself(self) -> None:
        names = _names(_job("deploy"))
        for name in ("Preview the plan", "Refuse to apply a plan carrying a delete or replace", "Apply"):
            self.assertIn(name, names)
        self.assertLess(names.index("Preview the plan"), names.index("Apply"))

    def test_the_deploy_jobs_gate_step_is_still_first_after_checkout(self) -> None:
        names = _names(_job("deploy"))
        self.assertEqual(names[1], "Refuse to deploy through an ungated environment")


class ThePlanJobCannotDeliver(unittest.TestCase):
    def test_it_holds_no_host_key_and_does_not_apply_or_ssh(self) -> None:
        code = _code(_job("plan"))
        for forbidden in ("APP_HOST_DEPLOY_KEY", "pulumi up", "ssh ", "deploy_key", "APP_HOST_SSH_ADDRESS"):
            self.assertNotIn(forbidden, code)

    def test_the_only_secrets_it_reads_are_the_four_state_ones_and_the_run_token(self) -> None:
        named = set(re.findall(r"secrets\.([A-Z0-9_]+)", _code(_job("plan"))))
        self.assertEqual(named, STATE_SECRETS | {"GITHUB_TOKEN"})

    def test_it_asks_for_no_write_scope(self) -> None:
        block = "\n".join(_job("plan"))
        match = re.search(r"    permissions:\n((?:      .*\n?)+)", block)
        self.assertIsNotNone(match)
        for line in match.group(1).splitlines():  # type: ignore[union-attr]
            self.assertRegex(line.strip(), r"^[a-z-]+: read$")

    def test_no_step_swallows_a_failure(self) -> None:
        code = _code(_job("plan"))
        self.assertNotIn("continue-on-error", code)
        self.assertNotIn("|| true", code)


class ThePlanJobMakesTheDeployJobsPlan(unittest.TestCase):
    def test_the_shared_steps_are_equal_to_the_character(self) -> None:
        for name in SHARED_STEPS:
            with self.subTest(step=name):
                self.assertEqual(
                    _code(_step_named(_job("plan"), name)),
                    _code(_step_named(_job("deploy"), name)),
                )

    def test_both_previews_keep_show_sames(self) -> None:
        # Without it Pulumi omits an unchanged GhostTenant from `steps`, which the
        # component's delete guard cannot tell from a component that stopped
        # registering its identity, and refuses.
        for job in ("plan", "deploy"):
            with self.subTest(job=job):
                self.assertIn("--show-sames", _code(_step_named(_job(job), "Preview the plan")))

    def test_the_steps_run_in_the_order_that_matters(self) -> None:
        names = _names(_job("plan"))
        order = [
            "Refuse a plan environment that any branch can use",
            "Log in to the Pulumi state backend",
            "Restore the stack's encryption salt",
            "Preview the plan",
            "Refuse to apply a plan carrying a delete or replace",
            "Print the plan",
        ]
        positions = [names.index(n) for n in order]
        self.assertEqual(positions, sorted(positions))

    def test_the_branch_check_runs_before_any_step_reads_a_state_secret(self) -> None:
        steps = _steps(_job("plan"))
        check = _names(_job("plan")).index("Refuse a plan environment that any branch can use")
        for index, step in enumerate(steps):
            if any(f"secrets.{name}" in _code(step) for name in STATE_SECRETS):
                self.assertLess(check, index)
        self.assertNotRegex(_code(steps[check]), r"secrets\.[A-Za-z_]")

    def test_no_run_body_spells_out_the_marker_the_approver_counts(self) -> None:
        self.assertNotIn("changeSummary=", _code(_job("plan")))

    def test_the_plan_is_printed_by_the_script_and_never_dumped_whole(self) -> None:
        code = _code(_step_named(_job("plan"), "Print the plan"))
        self.assertIn("scripts/summarise-plan.py preview.json", code)
        for dumped in ("json.tool", "cat preview.json", "GITHUB_STEP_SUMMARY"):
            self.assertNotIn(dumped, _code(_job("plan")))


def _run_guard(env, policies):
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, doc in (("env.json", env), ("policies.json", policies)):
            path = Path(d) / name
            path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
            paths.append(str(path))
        return subprocess.run(
            [sys.executable, "-I", str(MAIN_ONLY), *paths], capture_output=True, text=True, check=False
        )


class TheBranchPolicyGuard(unittest.TestCase):
    MAIN_ENV = {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
    MAIN_POLICIES = {"total_count": 1, "branch_policies": [{"name": "main", "type": "branch"}]}

    def test_main_alone_passes(self) -> None:
        self.assertEqual(_run_guard(self.MAIN_ENV, self.MAIN_POLICIES).returncode, 0)

    def test_everything_else_fails_closed(self) -> None:
        for name, env, policies in (
            ("open to every branch", {"deployment_branch_policy": None}, {"branch_policies": []}),
            ("wildcard", self.MAIN_ENV, {"branch_policies": [{"name": "*", "type": "branch"}]}),
            ("main and another", self.MAIN_ENV,
             {"branch_policies": [{"name": "main"}, {"name": "dev"}]}),
            ("not json", "<html>", self.MAIN_POLICIES),
            ("error body", {"message": "Not Found"}, {"message": "Not Found"}),
        ):
            with self.subTest(case=name):
                self.assertEqual(_run_guard(env, policies).returncode, 1)

    def test_a_missing_file_fails_closed(self) -> None:
        done = subprocess.run(
            [sys.executable, "-I", str(MAIN_ONLY), "/nonexistent/a.json", "/nonexistent/b.json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 1)

    def test_the_self_test_passes(self) -> None:
        done = subprocess.run([sys.executable, "-I", str(MAIN_ONLY), "--self-test"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertNotIn("FAIL", done.stdout)

    def test_the_step_reads_the_environment_the_job_declares(self) -> None:
        declared = _field(_job("plan"), "environment")
        body = _code(_step_named(_job("plan"), "Refuse a plan environment that any branch can use"))
        self.assertIn(f'environments/{declared}"', body)
        self.assertIn(f'environments/{declared}/deployment-branch-policies"', body)

    def test_the_refusal_step_calls_the_guard_and_stops_the_job_on_its_exit_status(self) -> None:
        # With the guard call replaced by `if false`, every other test in this
        # module stays green. The step must call the guard as the condition of an
        # `if` whose body exits 1, so a refusal ends the job before the secrets are read.
        body = _code(_step_named(_job("plan"), "Refuse a plan environment that any branch can use"))
        self.assertRegex(
            body,
            r'if ! python3 scripts/assert-plan-environment-main-only\.py "\$env_json" "\$policies_json"; then\n'
            r'\s*echo "::error::[^\n]*" >&2\n'
            r"\s*exit 1\n"
            r"\s*fi",
        )


if __name__ == "__main__":
    unittest.main()
