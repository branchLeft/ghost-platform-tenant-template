"""Tests for the plan job in infra-ci.yml and the branch-policy guard it runs.

See summarise-plan.md and assert-plan-environment-main-only.md. The workflow is
read as text (stdlib only), the way test_assert_environment_gated.py reads the
deploy job, so the credential-free `secret-guard` job runs these too.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from test_image_changed import (  # noqa: E402 -- after the path line above
    BASELINE_SHA,
    OLDER_SHA,
    FakeApi,
    fixture,
    ok,
)

WORKFLOW = SCRIPTS.parent / ".github" / "workflows" / "infra-ci.yml"
MAIN_ONLY = SCRIPTS / "assert-plan-environment-main-only.py"

SAVE_PLAN_STEP = "Save the update plan for the no-change check"
PLAN_STEP = "Decide whether the plan changes anything"
IMAGE_STEP = "Check the pinned image against the last deployed one"
NOOP_STEP = "Decide whether the deploy is needed"
HAS_CHANGES_OPERAND = "needs.plan.outputs.has_changes != 'false'"

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

    def test_the_plan_job_has_the_deploy_jobs_condition_less_the_has_changes_operand(self) -> None:
        # The two jobs are created together or not at all; the deploy job's one
        # extra operand is the verdict, and it is the last (see TheDeployJobSkipsOnlyOnAnExplicitFalse).
        self.assertEqual(_field(_job("plan"), "if") + f" && {HAS_CHANGES_OPERAND}", _field(_job("deploy"), "if"))

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

    def test_no_step_swallows_a_failure_but_the_best_effort_plan_save(self) -> None:
        code = _code(_job("plan"))
        self.assertNotIn("|| true", code)
        # The plan save is best effort because the decision step below it reads a
        # missing plan as "changes"; no other step in this job may carry the key.
        job = _job("plan")
        carriers = [
            _names(job)[index] for index, step in enumerate(_steps(job)) if "continue-on-error" in _code(step)
        ]
        self.assertEqual(carriers, [SAVE_PLAN_STEP])

    def test_the_deploy_job_swallows_no_failure_at_all(self) -> None:
        # Strengthened beyond the plan job: the deploy job's apply, guard and SSH steps
        # must each fail the job, so none may carry `continue-on-error` or `|| true`.
        code = _code(_job("deploy"))
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
        # The verdict step is the one that calls the shared action, found by its `uses:`
        # line, so a move of that call is caught whatever the step is named.
        job = _job("plan")
        names = _names(job)
        action = [name for name, step in zip(names, _steps(job)) if "plan-has-changes@" in _code(step)]
        self.assertEqual(action, [PLAN_STEP])
        order = [
            "Refuse a plan environment that any branch can use",
            "Log in to the Pulumi state backend",
            "Restore the stack's encryption salt",
            "Preview the plan",
            "Refuse to apply a plan carrying a delete or replace",
            "Print the plan",
            SAVE_PLAN_STEP,
            action[0],
            IMAGE_STEP,
            NOOP_STEP,
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
        for dumped in ("json.tool", "cat preview.json"):
            self.assertNotIn(dumped, _code(_job("plan")))

    def test_no_other_script_names_the_saved_plan(self) -> None:
        # The saved plan holds secrets-provider ciphertext. Which steps may name it is
        # the check-apply-gate action's (checked in its own repo and in the secret-guard
        # job); this holds only that no script in this repo reads it.
        for script in sorted(SCRIPTS.glob("*.py")):
            if script.name.startswith("test_"):
                continue
            self.assertNotIn("preview-plan", script.read_text(encoding="utf-8"), script.name)

    def test_only_the_deciding_step_writes_the_job_summary(self) -> None:
        job = _job("plan")
        writers = [
            _names(job)[index] for index, step in enumerate(_steps(job)) if "GITHUB_STEP_SUMMARY" in _code(step)
        ]
        self.assertEqual(writers, [NOOP_STEP])


def _run_text(step: list[str]) -> str:
    """The shell text of a step's `run:`, a one-line value or a `|` block."""
    for index, line in enumerate(step):
        block = re.fullmatch(r"        run: \|\s*", line)
        if block:
            body: list[str] = []
            for rest in step[index + 1 :]:
                if rest.strip() and not rest.startswith(" " * 10):
                    break
                body.append(rest[10:])
            return "\n".join(body) + "\n"
        single = re.fullmatch(r"        run: (\S.*)", line)
        if single:
            return single.group(1) + "\n"
    raise AssertionError("the step has no run:")


class TheDeployJobSkipsOnlyOnAnExplicitFalse(unittest.TestCase):
    def test_the_gates_are_untouched(self) -> None:
        deploy = _job("deploy")
        self.assertEqual(_field(deploy, "environment"), "production")
        self.assertEqual(_field(deploy, "needs"), "[typecheck, secret-guard, plan]")
        self.assertRegex("\n".join(deploy), r"group: deploy-\S+")
        self.assertIn("cancel-in-progress: false", "\n".join(deploy))
        self.assertEqual(_names(deploy)[1], "Refuse to deploy through an ungated environment")


class ThePlanJobPublishesOneVerdictFromOneStep(unittest.TestCase):
    def test_only_the_deciding_step_writes_has_changes_to_the_job_output(self) -> None:
        job = _job("plan")
        for index, step in enumerate(_steps(job)):
            name = _names(job)[index]
            code = _code(step)
            if name == NOOP_STEP:
                self.assertEqual(code.count("GITHUB_OUTPUT"), 1)
                self.assertIn('echo "has_changes=$has_changes" >> "$GITHUB_OUTPUT"', code)
                self.assertIn("id: noop", code)
            else:
                self.assertNotIn("GITHUB_OUTPUT", code, name)
                self.assertNotIn("id: noop", code, name)

    def test_neither_half_writes_the_job_output_or_summary(self) -> None:
        # The action step writes its headline to a file and not to the summary.
        self.assertIn("write-summary: 'false'", _code(_step_named(_job("plan"), PLAN_STEP)))
        image = _code(_step_named(_job("plan"), IMAGE_STEP))
        self.assertIn('"$RUNNER_TEMP/', image)
        for name in (PLAN_STEP, IMAGE_STEP):
            code = _code(_step_named(_job("plan"), name))
            self.assertNotIn("GITHUB_OUTPUT", code)
            self.assertNotIn("GITHUB_STEP_SUMMARY", code)

    def test_the_image_check_runs_isolated_with_the_runs_own_token_and_no_other(self) -> None:
        step = _step_named(_job("plan"), IMAGE_STEP)
        code = _code(step)
        self.assertIn("python3 -I scripts/image-changed.py", code)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", code)
        self.assertNotIn("secrets.", code)

    def test_the_plan_job_gains_no_permission_and_no_environment(self) -> None:
        block = re.search(r"    permissions:\n((?:      .*\n?)+)", "\n".join(_job("plan")))
        self.assertIsNotNone(block)
        granted = sorted(line.strip() for line in block.group(1).splitlines())  # type: ignore[union-attr]
        self.assertEqual(granted, ["actions: read", "contents: read", "packages: read"])
        self.assertEqual(_field(_job("plan"), "environment"), "production-plan")

    def test_the_plan_save_uses_the_previews_credentials_and_the_state_backend_only(self) -> None:
        save = _code(_step_named(_job("plan"), SAVE_PLAN_STEP))
        preview = _code(_step_named(_job("plan"), "Preview the plan"))
        self.assertIn("--save-plan preview-plan.json", save)
        self.assertEqual(_env_block(save), _env_block(preview))


def _env_block(code: str) -> str:
    match = re.search(r"        env:\n((?:          .*\n?)+)", code + "\n")
    assert match is not None, code
    return match.group(1)


def _config_name() -> str:
    """The stack config file the image step is told to read, as the workflow spells it
    (a generated repo carries its own name where the template carries the placeholder)."""
    match = re.search(r"--config (\S+)", _run_text(_step_named(_job("plan"), IMAGE_STEP)))
    assert match is not None
    return match.group(1).replace("__TENANT_NAME__", "blog")


def _clean_checkout(work: Path, preview: str | None, plan: str | None, config: str | None) -> None:
    """The files the steps before the decision leave in the workspace, as fixtures.

    The saved plan is not placed here: the plan-save step writes it, through a stub
    `pulumi` that copies `stub-plan.json` to the path it is given (or fails, with none)."""
    os.symlink(SCRIPTS, work / "scripts")
    for name, source in (("preview.json", preview), ("stub-plan.json", plan), (_config_name(), config)):
        if source is not None:
            (work / name).write_bytes(fixture(source))


# Stands in for `pulumi`: logs the call, makes a noise on stdout the step must discard,
# and writes the plan fixture where `--save-plan` points, or exits 1 when there is none.
STUB_PULUMI = """#!/bin/bash
echo "$*" >> "$STUB_LOG"
echo "PLAN_STDOUT_MARKER_MUST_BE_DISCARDED"
[ "$1" = "preview" ] || exit 99
path=
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--save-plan" ]; then path="$2"; fi
  shift
done
[ -n "$path" ] && [ -f stub-plan.json ] || exit 1
cp stub-plan.json "$path"
"""


def _action_verdict(preview: str | None, plan: str | None) -> str:
    """What the shared plan-has-changes action reports for these fixtures. Its own tests
    live in branchLeft/github-workflows; here the verdict only feeds the combining step."""
    return "false" if (preview, plan) == ("preview-all-same.json", "plan-all-same.json") else "true"


def _combine_env(step: list[str], verdict: str, summary: Path) -> dict[str, str]:
    """The combining step's own `env:` block, with the action's outputs filled in as the
    runner would fill them: the verdict (empty when the action wrote none) and the path."""
    subs = {
        "${{ steps.plan.outputs.has_changes }}": verdict,
        "${{ steps.plan.outputs.summary_file }}": str(summary),
    }
    found: dict[str, str] = {}
    in_env = False
    for line in step:
        if line == "        env:":
            in_env = True
            continue
        if not in_env:
            continue
        match = re.fullmatch(r"          ([A-Z_]+): (.*)", line)
        if not match:
            break
        value = match.group(2)
        for before, after in subs.items():
            value = value.replace(before, after)
        found[match.group(1)] = value
    return found


class TheStepsAsTheWorkflowRunsThem(unittest.TestCase):
    """The real `run:` bodies of the deciding steps (the plan save, against a stub
    `pulumi`; the image check, against a local API; the combine), run in order under bash,
    so what is asserted is what CI would execute. The shared action's step is not run here:
    its verdict is fed to the combining step through the env binding the workflow declares.
    `continue-on-error` is the runner's, not bash's: a failing save step is run on regardless,
    as the runner would."""

    PIN_A = "stack-config-pin-a.yaml"
    PIN_B = "stack-config-pin-b.yaml"
    CLEAN_PREVIEW = "preview-all-same.json"
    CLEAN_PLAN = "plan-all-same.json"
    ALL_STEPS = (SAVE_PLAN_STEP, IMAGE_STEP, NOOP_STEP)

    def run_steps(self, *, preview, plan, current, baseline, steps=ALL_STEPS, verdict=None, **api_kwargs):
        if verdict is None:
            verdict = _action_verdict(preview, plan)
        success = api_kwargs.pop("success", ok("runs-success-12-10.json"))
        recent = api_kwargs.pop("recent", ok("runs-recent-quiet.json"))
        contents = {BASELINE_SHA: (200, fixture(baseline))} if isinstance(baseline, str) else baseline
        api = FakeApi(success, recent, contents, config_name=_config_name())
        self.addCleanup(api.close)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            temp = work / "runner-temp"
            temp.mkdir()
            _clean_checkout(work, preview, plan, current)
            stubs = work / "stub-bin"
            stubs.mkdir()
            (stubs / "pulumi").write_text(STUB_PULUMI, encoding="utf-8")
            (stubs / "pulumi").chmod(0o755)
            stub_log = work / "pulumi-calls.log"
            github_output = work / "github-output"
            github_summary = work / "github-summary"
            summary_file = work / "action-headline.txt"
            summary_file.write_text("Action headline\n", encoding="utf-8")
            environ = {
                "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
                "STUB_LOG": str(stub_log),
                "RUNNER_TEMP": str(temp),
                "GITHUB_OUTPUT": str(github_output),
                "GITHUB_STEP_SUMMARY": str(github_summary),
                "GITHUB_TOKEN": "stub-token-never-real",
                "GITHUB_REPOSITORY": "example/tenant",
                "GITHUB_RUN_ID": "9003",
                "GITHUB_RUN_NUMBER": "13",
                "GITHUB_API_URL": api.url,
            }
            codes = {}
            stdouts = {}
            for name in steps:
                step = _step_named(_job("plan"), name)
                text = _run_text(step).replace("__TENANT_NAME__", "blog")
                step_environ = dict(environ)
                if name == NOOP_STEP:
                    step_environ.update(_combine_env(step, verdict, summary_file))
                done = subprocess.run(["bash", "-e", "-c", text], cwd=work, capture_output=True, text=True, env=step_environ, check=False)
                codes[name] = done.returncode
                stdouts[name] = done.stdout
            self.stub_calls = stub_log.read_text(encoding="utf-8").splitlines() if stub_log.exists() else []
            self.stdouts = stdouts
            output = github_output.read_text(encoding="utf-8") if github_output.exists() else ""
            summary = github_summary.read_text(encoding="utf-8") if github_summary.exists() else ""
            return codes, output.splitlines(), summary.splitlines()

    def assertSkips(self, result) -> None:
        codes, output, summary = result
        self.assertEqual(output, ["has_changes=false"], (codes, summary))
        self.assertTrue(summary[0].startswith("No changes: the deploy job is skipped"), summary)

    def assertAsks(self, result, first_line_start="Approval required: ") -> None:
        codes, output, summary = result
        self.assertEqual(output, ["has_changes=true"], (codes, summary))
        self.assertTrue(summary[0].startswith(first_line_start), summary)

    def test_a_clean_plan_and_an_unchanged_image_skips_the_deploy(self) -> None:
        for name, pin in (("pin a", self.PIN_A), ("pin b", self.PIN_B)):
            with self.subTest(case=name):
                result = self.run_steps(preview=self.CLEAN_PREVIEW, plan=self.CLEAN_PLAN, current=pin, baseline=pin)
                self.assertSkips(result)
                self.assertEqual(result[0], {SAVE_PLAN_STEP: 0, IMAGE_STEP: 0, NOOP_STEP: 0})

    def test_the_plan_save_runs_a_preview_that_saves_the_plan_and_discards_its_output(self) -> None:
        self.run_steps(
            preview=self.CLEAN_PREVIEW, plan=self.CLEAN_PLAN, current=self.PIN_A, baseline=self.PIN_A, steps=(SAVE_PLAN_STEP,)
        )
        stack = _config_name().removeprefix("Pulumi.").removesuffix(".yaml")  # `blog` here, the slug in a generated repo
        self.assertEqual(self.stub_calls, [f"preview --stack {stack} --save-plan preview-plan.json"])
        self.assertEqual(self.stdouts[SAVE_PLAN_STEP], "")

    def test_a_failed_run_between_two_successes_asks_over_a_clean_plan(self) -> None:
        # The reproduced shape through the real steps: success pin A (run 10), failure pin B (run 12), this run pin A.
        contents = {
            OLDER_SHA: (200, fixture(self.PIN_A)),
            "1212121212121212121212121212121212121212": (200, fixture(self.PIN_B)),
        }
        result = self.run_steps(
            preview=self.CLEAN_PREVIEW,
            plan=self.CLEAN_PLAN,
            current=self.PIN_A,
            baseline=contents,
            success=ok("runs-success-only-10.json"),
            recent=ok("runs-recent-failed-between.json"),
        )
        self.assertAsks(result, "Approval required: the pinned image is not shown")
        self.assertIn("Image check: intervening-run-pin-differs", result[2])

    def test_an_image_only_change_with_an_all_same_preview_asks(self) -> None:
        # The sabotage case that matters: the Pulumi side reads a clean plan, so only the image clause asks.
        for name, current, baseline in (("a to b", self.PIN_B, self.PIN_A), ("b to a", self.PIN_A, self.PIN_B)):
            with self.subTest(case=name):
                result = self.run_steps(preview=self.CLEAN_PREVIEW, plan=self.CLEAN_PLAN, current=current, baseline=baseline)
                self.assertAsks(result, "Approval required: the pinned image is not shown")
                self.assertIn("Image check: pin-differs", result[2])

    def test_a_changed_plan_asks_whatever_the_image_says(self) -> None:
        result = self.run_steps(preview="preview-one-update.json", plan=self.CLEAN_PLAN, current=self.PIN_A, baseline=self.PIN_A)
        self.assertAsks(result, "Approval required: the plan changes something")
        self.assertIn("Image check: pin-equal", result[2])

    def test_a_missing_saved_plan_asks_even_with_equal_pins(self) -> None:
        result = self.run_steps(preview=self.CLEAN_PREVIEW, plan=None, current=self.PIN_A, baseline=self.PIN_A)
        self.assertAsks(result)

    def test_every_image_side_failure_asks_even_over_a_clean_plan(self) -> None:
        good = {BASELINE_SHA: (200, fixture(self.PIN_A))}
        cases = (
            ("no earlier success", {"success": ok("runs-empty.json"), "recent": ok("runs-recent-only-current.json")}, good),
            ("api error", {"success": (500, b"{}")}, good),
            ("not json", {"recent": (200, b"<html>")}, good),
            ("missing line", {}, {BASELINE_SHA: (200, fixture("stack-config-no-pin.yaml"))}),
            ("duplicate line", {}, {BASELINE_SHA: (200, fixture("stack-config-two-pins.yaml"))}),
            ("pending earlier run", {"success": ok("runs-success-only-10.json"), "recent": ok("runs-recent-pending.json")}, {OLDER_SHA: (200, fixture(self.PIN_A))}),
            ("unreadable baseline file", {}, {}),
        )
        for name, api, contents in cases:
            with self.subTest(case=name):
                result = self.run_steps(preview=self.CLEAN_PREVIEW, plan=self.CLEAN_PLAN, current=self.PIN_A, baseline=contents, **api)
                self.assertAsks(result, "Approval required: the pinned image is not shown")

    def test_a_verdict_file_that_was_never_written_asks(self) -> None:
        # The action's verdict alone, then the image step alone: the missing half reads as "changes".
        for name, steps, verdict in (
            ("no image verdict", (SAVE_PLAN_STEP, NOOP_STEP), "false"),
            ("no plan verdict", (IMAGE_STEP, NOOP_STEP), ""),
            ("neither", (NOOP_STEP,), ""),
        ):
            with self.subTest(case=name):
                result = self.run_steps(
                    preview=self.CLEAN_PREVIEW, plan=self.CLEAN_PLAN, current=self.PIN_A, baseline=self.PIN_A,
                    steps=steps, verdict=verdict,
                )
                self.assertAsks(result)
                self.assertEqual(result[1], ["has_changes=true"])


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
