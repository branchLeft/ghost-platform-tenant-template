"""Tests for generate-pnpm-lockfile.yml, update mode and the refusals both modes share.

See ../.github/workflows/generate-pnpm-lockfile.md. The workflow is read as
text (stdlib only). Each step's `run:` body is cut out and executed under bash
with stub tools, and the update-mode gate is cut out of its heredoc and run as
the runner runs it, so what is asserted is the step CI runs, not a copy.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
WORKFLOW = ROOT / ".github" / "workflows" / "generate-pnpm-lockfile.yml"
REAL_LOCK = ROOT / "pnpm-lock.yaml"

MODE_STEP = "Choose convert or update mode from the lockfiles on this branch"
SHAPE_STEP = "Validate branch shape and refuse main"
RULESET_STEP = "Refuse a branch a ruleset currently protects"
RESOLVE_STEP = "Re-resolve pnpm-lock.yaml in an isolated directory (update mode)"
GUARD_STEP = "Refuse a lockfile change that no bumped dependency explains"
APPLY_STEP = "Apply the updated lockfile"
SWEEP_STEP = "Refuse any change beyond pnpm-lock.yaml"
COMMIT_STEP = "Commit the lockfile via the GitHub API, if it changed"

CONVERT_STEPS = (
    "Generate pnpm-lock.yaml in an isolated directory",
    "Refuse a version pnpm resolved that package-lock.json did not lock",
    "Apply the generated lockfile",
    "Refuse any change beyond the lockfile swap",
)
UPDATE_STEPS = (RESOLVE_STEP, GUARD_STEP, APPLY_STEP, SWEEP_STEP)

HEADER = "lockfileVersion: '9.0'\n\nsettings:\n  autoInstallPeers: true\n  excludeLinksFromLockfile: false\n"


def _job(name: str) -> list[str]:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if re.fullmatch(rf"  {name}:\s*", line))
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"  \S", lines[i])), len(lines))
    return lines[start:end]


def _steps(job: str) -> dict[str, list[str]]:
    steps: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in _job(job):
        found = re.match(r"      - name: (.*)$", line)
        if found:
            current = steps.setdefault(found.group(1), [])
        if current is not None:
            current.append(line)
    return steps


def _step(job: str, name: str) -> list[str]:
    return _steps(job)[name]


def _run_body(job: str, name: str) -> str:
    lines = _step(job, name)
    first = next(i for i, line in enumerate(lines) if line == "        run: |")
    body: list[str] = []
    for line in lines[first + 1 :]:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line[10:] if line.strip() else "")
    return "\n".join(body) + "\n"


def _guard_source() -> str:
    body = _run_body("generate", GUARD_STEP)
    start = body.index("<<'PYEOF'\n") + len("<<'PYEOF'\n")
    return body[start : body.index("\nPYEOF")] + "\n"


def _load_guard() -> dict:
    namespace: dict = {"__name__": "lockfile_guard"}
    exec(compile(_guard_source(), "lockfile-guard", "exec"), namespace)
    return namespace


def _bash(body: str, cwd: pathlib.Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    full = {"PATH": os.environ["PATH"], "HOME": str(cwd), **env}
    return subprocess.run(["bash", "-c", body], cwd=cwd, env=full, capture_output=True, text=True, check=False)


def _q(key: str) -> str:
    return f"'{key}'" if key.startswith("@") else key


def build_lock(
    roots: dict[str, tuple[str, str]],
    snapshots: dict[str, dict[str, str]],
    integrity: dict[str, str] | None = None,
    header: str = HEADER,
) -> str:
    """A v9 lockfile in pnpm's layout. `snapshots` maps a snapshot key to its dependencies."""
    integrity = integrity or {}
    out = [header, "importers:\n", "  .:\n    dependencies:"]
    for name, (spec, version) in roots.items():
        out.append(f"      {_q(name)}:\n        specifier: {spec}\n        version: {version}")
    out.append("\npackages:\n")
    for pkg in sorted({k.split("(")[0] for k in snapshots}):
        out.append(f"  {_q(pkg)}:\n    resolution: {{integrity: {integrity.get(pkg, 'sha512-' + pkg)}}}\n")
    out.append("snapshots:\n")
    for snap, deps in snapshots.items():
        if not deps:
            out.append(f"  {_q(snap)}: {{}}\n")
            continue
        out.append(f"  {_q(snap)}:\n    dependencies:")
        out.extend(f"      {_q(dep)}: {ver}" for dep, ver in deps.items())
        out.append("")
    return "\n".join(out) + "\n"


TENANT7 = "@scope/tenant@7.0.0(typescript@5.9.3)"
TENANT8 = "@scope/tenant@8.0.0(typescript@5.9.3)"
BASE_ROOTS = {
    "@scope/tenant": ("7.0.0", "7.0.0(typescript@5.9.3)"),
    "left-pad": ("^1.0.0", "1.3.0"),
    "typescript": ("^5.9.0", "5.9.3"),
}
BASE_SNAPS = {
    TENANT7: {"dep-a": "1.0.0", "typescript": "5.9.3"},
    "dep-a@1.0.0": {"shared": "1.0.0"},
    "shared@1.0.0": {},
    "left-pad@1.3.0": {},
    "typescript@5.9.3": {},
}
BUMPED_ROOTS = {**BASE_ROOTS, "@scope/tenant": ("8.0.0", "8.0.0(typescript@5.9.3)")}
BUMPED_SNAPS = {
    TENANT8: {"dep-a": "2.0.0", "typescript": "5.9.3"},
    "dep-a@2.0.0": {"shared": "1.0.0", "fresh": "1.0.0"},
    "fresh@1.0.0": {},
    "shared@1.0.0": {},
    "left-pad@1.3.0": {},
    "typescript@5.9.3": {},
}
MANIFEST = {
    "dependencies": {"@scope/tenant": "8.0.0"},
    "devDependencies": {"left-pad": "^1.0.0", "typescript": "^5.9.0"},
}

# Produced by a real `pnpm install --lockfile-only` before and after bumping
# is-odd 3.0.0 -> 2.0.0, so the parser is also shown a lockfile pnpm wrote.
REAL_OLD = (
    HEADER
    + """
importers:

  .:
    dependencies:
      is-odd:
        specifier: 3.0.0
        version: 3.0.0
      semver:
        specifier: ^7.5.0
        version: 7.8.6
    devDependencies:
      ms:
        specifier: ^2.0.0
        version: 2.1.3

packages:

  is-number@6.0.0:
    resolution: {integrity: sha512-Wu1VHeILBK8KAWJUAiSZQX94GmOE45Rg6/538fKwiloUu21KncEkYGPqob2oSZ5mUT73vLGrHQjKw3KMPwfDzg==}
    engines: {node: '>=0.10.0'}

  is-odd@3.0.0:
    resolution: {integrity: sha512-204vE5IJ0Cd6pA6x1dMyLooGk6/xeKuq90imFuJN/ndMDBP4Sk9tJpBlTedHPvt6KDbtTDTsjVzzgctFqNV7FQ==}
    engines: {node: '>=4'}

  ms@2.1.3:
    resolution: {integrity: sha512-6FlzubTLZG3J2a/NVCAleEhjzq5oxgHyaCU9yYXvcLsvoVaHJq/s5xXI6/XXP6tz7R9xAOtHnSO/tXtF3WRTlA==}

  semver@7.8.6:
    resolution: {integrity: sha512-9lpSSU4Mly3mrtruwLs1eLK6+gzbzqeBFwRNjIjE+9LuDpWdYctrQlQ3ej1Ilgi7gknpsTRnPoLP8DpCHqHIJw==}
    engines: {node: '>=10'}
    hasBin: true

snapshots:

  is-number@6.0.0: {}

  is-odd@3.0.0:
    dependencies:
      is-number: 6.0.0

  ms@2.1.3: {}

  semver@7.8.6: {}
"""
)
REAL_NEW = (
    REAL_OLD.replace("specifier: 3.0.0\n        version: 3.0.0", "specifier: 2.0.0\n        version: 2.0.0")
    .replace(
        "is-number@6.0.0:\n    resolution: {integrity: sha512-Wu1VHeILBK8KAWJUAiSZQX94GmOE45Rg6/538fKwiloUu21KncEkYGPqob2oSZ5mUT73vLGrHQjKw3KMPwfDzg==}",
        "is-number@4.0.0:\n    resolution: {integrity: sha512-rSklcAIlf1OmFdyAqbnWTLVelsQ58uvZ66S/ZyawjWqIviTWCjg2PzVGw8WUA+nNuPTqb4wgA+NszrJ+08LlgQ==}",
    )
    .replace(
        "is-odd@3.0.0:\n    resolution: {integrity: sha512-204vE5IJ0Cd6pA6x1dMyLooGk6/xeKuq90imFuJN/ndMDBP4Sk9tJpBlTedHPvt6KDbtTDTsjVzzgctFqNV7FQ==}\n    engines: {node: '>=4'}",
        "is-odd@2.0.0:\n    resolution: {integrity: sha512-OTiixgpZAT1M4NHgS5IguFp/Vz2VI3U7Goh4/HA1adtwyLtSBrxYlcSYkhpAE07s4fKEcjrFxyvtQBND4vFQyQ==}\n    engines: {node: '>=0.10.0'}",
    )
    .replace("is-number@6.0.0: {}", "is-number@4.0.0: {}")
    .replace("is-odd@3.0.0:\n    dependencies:\n      is-number: 6.0.0", "is-odd@2.0.0:\n    dependencies:\n      is-number: 4.0.0")
)
REAL_MANIFEST = {"dependencies": {"is-odd": "2.0.0", "semver": "^7.5.0"}, "devDependencies": {"ms": "^2.0.0"}}


class GuardCase(unittest.TestCase):
    """Runs the update-mode gate exactly as the step does: source on stdin, three paths in argv."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def run_guard(self, old: str, new: str, manifest: dict) -> subprocess.CompletedProcess[str]:
        (self.dir / "old.yaml").write_text(old, encoding="utf-8")
        (self.dir / "new.yaml").write_text(new, encoding="utf-8")
        (self.dir / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-", str(self.dir / "old.yaml"), str(self.dir / "new.yaml"), str(self.dir / "package.json")],
            input=_guard_source(),
            capture_output=True,
            text=True,
            check=False,
        )

    def assert_refused(self, result: subprocess.CompletedProcess[str], fragment: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("::error::", result.stdout)
        self.assertIn(fragment, result.stdout)


class TheUpdateGuardAccepts(GuardCase):
    def test_a_bump_whose_changes_all_sit_under_the_bumped_dependency(self) -> None:
        result = self.run_guard(
            build_lock(BASE_ROOTS, BASE_SNAPS), build_lock(BUMPED_ROOTS, BUMPED_SNAPS), MANIFEST
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("bumped direct dependencies: ['@scope/tenant']", result.stdout)
        self.assertIn("dep-a@1.0.0", result.stdout)
        self.assertIn("fresh@1.0.0", result.stdout)

    def test_a_lockfile_pnpm_itself_wrote(self) -> None:
        result = self.run_guard(REAL_OLD, REAL_NEW, REAL_MANIFEST)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("is-number@4.0.0", result.stdout)


class TheUpdateGuardRefuses(GuardCase):
    def test_a_lockfile_that_did_not_change(self) -> None:
        old = build_lock(BASE_ROOTS, BASE_SNAPS)
        self.assert_refused(self.run_guard(old, old, MANIFEST), "did not change")

    def test_a_package_json_that_matches_the_old_lockfile(self) -> None:
        unchanged_manifest = {
            "dependencies": {"@scope/tenant": "7.0.0"},
            "devDependencies": {"left-pad": "^1.0.0", "typescript": "^5.9.0"},
        }
        moved = {**BASE_SNAPS, "left-pad@1.3.0": {"shared": "1.0.0"}}
        self.assert_refused(
            self.run_guard(build_lock(BASE_ROOTS, BASE_SNAPS), build_lock(BASE_ROOTS, moved), unchanged_manifest),
            "no dependency was bumped",
        )

    def test_an_unrelated_direct_dependency_that_moved_version(self) -> None:
        roots = {**BUMPED_ROOTS, "left-pad": ("^1.0.0", "1.3.1")}
        snaps = {k: v for k, v in BUMPED_SNAPS.items() if k != "left-pad@1.3.0"} | {"left-pad@1.3.1": {}}
        self.assert_refused(
            self.run_guard(build_lock(BASE_ROOTS, BASE_SNAPS), build_lock(roots, snaps), MANIFEST),
            "left-pad has an unchanged specifier but resolved",
        )

    def test_an_unrelated_transitive_version_that_moved(self) -> None:
        old_snaps = {**BASE_SNAPS, "left-pad@1.3.0": {"pad-helper": "1.0.0"}, "pad-helper@1.0.0": {}}
        new_snaps = {**BUMPED_SNAPS, "left-pad@1.3.0": {"pad-helper": "1.1.0"}, "pad-helper@1.1.0": {}}
        self.assert_refused(
            self.run_guard(build_lock(BASE_ROOTS, old_snaps), build_lock(BUMPED_ROOTS, new_snaps), MANIFEST),
            "left-pad was not bumped but its dependency tree moved",
        )

    def test_a_package_added_that_no_bumped_dependency_reaches(self) -> None:
        snaps = {**BUMPED_SNAPS, "stowaway@9.9.9": {}}
        self.assert_refused(
            self.run_guard(build_lock(BASE_ROOTS, BASE_SNAPS), build_lock(BUMPED_ROOTS, snaps), MANIFEST),
            "no bumped dependency reaches: ['stowaway@9.9.9']",
        )

    def test_a_package_dropped_that_no_bumped_dependency_reached(self) -> None:
        old_snaps = {**BASE_SNAPS, "orphan@1.0.0": {}}
        self.assert_refused(
            self.run_guard(build_lock(BASE_ROOTS, old_snaps), build_lock(BUMPED_ROOTS, BUMPED_SNAPS), MANIFEST),
            "orphan@1.0.0",
        )

    def test_a_kept_version_whose_integrity_changed(self) -> None:
        new = build_lock(BUMPED_ROOTS, BUMPED_SNAPS, integrity={"shared@1.0.0": "sha512-tampered"})
        self.assert_refused(
            self.run_guard(build_lock(BASE_ROOTS, BASE_SNAPS), new, MANIFEST),
            "shared@1.0.0 kept its version but its resolution or metadata changed",
        )

    def test_a_lockfile_version_or_settings_change(self) -> None:
        header = HEADER.replace("autoInstallPeers: true", "autoInstallPeers: false")
        self.assert_refused(
            self.run_guard(
                build_lock(BASE_ROOTS, BASE_SNAPS), build_lock(BUMPED_ROOTS, BUMPED_SNAPS, header=header), MANIFEST
            ),
            "settings changed",
        )

    def test_importer_specifiers_that_disagree_with_package_json(self) -> None:
        roots = {**BUMPED_ROOTS, "@scope/tenant": ("^8.0.0", "8.0.0(typescript@5.9.3)")}
        self.assert_refused(
            self.run_guard(build_lock(BASE_ROOTS, BASE_SNAPS), build_lock(roots, BUMPED_SNAPS), MANIFEST),
            "do not match package.json",
        )

    def test_a_second_importer(self) -> None:
        new = build_lock(BUMPED_ROOTS, BUMPED_SNAPS).replace("packages:\n", "  other:\n    dependencies: {}\n\npackages:\n", 1)
        self.assert_refused(self.run_guard(build_lock(BASE_ROOTS, BASE_SNAPS), new, MANIFEST), "single root importer")

    def test_a_dependency_with_no_snapshot(self) -> None:
        snaps = {k: v for k, v in BUMPED_SNAPS.items() if k != "fresh@1.0.0"}
        self.assert_refused(
            self.run_guard(build_lock(BASE_ROOTS, BASE_SNAPS), build_lock(BUMPED_ROOTS, snaps), MANIFEST),
            "has no snapshot",
        )

    def test_the_real_pair_with_an_unrelated_version_moved(self) -> None:
        tampered = REAL_NEW.replace("semver@7.8.6", "semver@7.8.7").replace("version: 7.8.6", "version: 7.8.7")
        self.assert_refused(self.run_guard(REAL_OLD, tampered, REAL_MANIFEST), "semver has an unchanged specifier")


class TheParserReadsTheRepositoryLockfile(unittest.TestCase):
    def test_every_package_is_reached_from_a_root(self) -> None:
        guard = _load_guard()
        lock = guard["parse_lock"](str(REAL_LOCK))
        roots = guard["roots_of"](lock, "pnpm-lock.yaml")
        self.assertGreater(len(roots), 0)
        reached = guard["closure"](lock, list(roots.items()))
        self.assertEqual(reached, set(lock["packages"]))


class TheWorkflowShape(unittest.TestCase):
    def test_it_is_dispatch_only_and_the_generate_job_waits_on_validate(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        trigger = text[text.index("\non:\n") : text.index("\npermissions: {}")]
        self.assertEqual(re.findall(r"^  (\w+):", trigger, re.M), ["workflow_dispatch"])
        generate = "\n".join(_job("generate"))
        self.assertIn("needs: validate", generate)
        self.assertIn("ref: refs/heads/${{ needs.validate.outputs.branch }}", generate)

    def test_each_mode_runs_only_its_own_steps(self) -> None:
        for name in CONVERT_STEPS:
            self.assertIn("        if: steps.mode.outputs.mode == 'convert'", _step("generate", name), name)
        for name in UPDATE_STEPS:
            self.assertIn("        if: steps.mode.outputs.mode == 'update'", _step("generate", name), name)

    def test_no_run_body_interpolates_an_expression(self) -> None:
        for job in ("validate", "generate"):
            for name in _steps(job):
                if any(line == "        run: |" for line in _step(job, name)):
                    self.assertNotIn("${{", _run_body(job, name), name)

    def test_the_update_gate_runs_before_the_lockfile_is_applied_and_committed(self) -> None:
        order = list(_steps("generate"))
        self.assertLess(order.index(RESOLVE_STEP), order.index(GUARD_STEP))
        self.assertLess(order.index(GUARD_STEP), order.index(APPLY_STEP))
        self.assertLess(order.index(APPLY_STEP), order.index(SWEEP_STEP))
        self.assertLess(order.index(SWEEP_STEP), order.index(COMMIT_STEP))

    def test_update_mode_commits_additions_only(self) -> None:
        body = _run_body("generate", COMMIT_STEP)
        update = body[body.index("update)") : body.index("*)")]
        convert = body[body.index("convert)") : body.index("update)")]
        self.assertNotIn("deletions", update)
        self.assertIn('additions: [{path: "pnpm-lock.yaml", contents: $content}]', update)
        self.assertIn('deletions: [{path: "package-lock.json"}]', convert)
        self.assertIn("createCommitOnBranch", update)
        self.assertIn(".commit.verification.verified", body)
        self.assertNotIn("git push", "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#")))

    def test_update_mode_resolves_without_scripts_in_an_isolated_directory(self) -> None:
        body = _run_body("generate", RESOLVE_STEP)
        self.assertIn('WORKDIR="$RUNNER_TEMP/', body)
        self.assertIn("pnpm install --lockfile-only --no-frozen-lockfile --ignore-scripts", body)
        self.assertNotIn("cp .npmrc", body)


class TheSharedRefusals(unittest.TestCase):
    """Validate runs before either mode, so a refusal here refuses both."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def shape(self, branch: str) -> subprocess.CompletedProcess[str]:
        out = self.dir / "output"
        out.write_text("", encoding="utf-8")
        return _bash(_run_body("validate", SHAPE_STEP), self.dir, {"BRANCH": branch, "GITHUB_OUTPUT": str(out)})

    def ruleset(self, response: str, exit_code: int) -> subprocess.CompletedProcess[str]:
        bin_dir = self.dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        gh = bin_dir / "gh"
        gh.write_text(f"#!/bin/sh\necho '{response}'\nexit {exit_code}\n", encoding="utf-8")
        gh.chmod(0o755)
        env = {"BRANCH": "feature", "REPO": "o/r", "GH_TOKEN": "x", "PATH": f"{bin_dir}:{os.environ['PATH']}"}
        return _bash(_run_body("validate", RULESET_STEP), self.dir, env)

    def test_main_and_master_are_refused(self) -> None:
        for branch in ("main", "master"):
            result = self.shape(branch)
            self.assertEqual(result.returncode, 1, branch)
            self.assertIn("refused outright", result.stdout)

    def test_a_qualified_ref_is_refused(self) -> None:
        self.assertEqual(self.shape("refs/heads/main").returncode, 1)

    def test_a_feature_branch_passes(self) -> None:
        self.assertEqual(self.shape("ws1799-secrets-on-host").returncode, 0)

    def test_a_branch_a_ruleset_protects_is_refused(self) -> None:
        result = self.ruleset('[{"type":"pull_request"}]', 0)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ruleset rule(s) currently apply", result.stdout)

    def test_an_unreadable_ruleset_is_treated_as_protected(self) -> None:
        self.assertEqual(self.ruleset("", 1).returncode, 1)

    def test_an_unprotected_branch_passes(self) -> None:
        self.assertEqual(self.ruleset("[]", 0).returncode, 0)


class TheUpdateSteps(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def mode(self, files: tuple[str, ...]) -> tuple[subprocess.CompletedProcess[str], str]:
        work = self.dir / "mode"
        work.mkdir(exist_ok=True)
        for old in work.iterdir():
            old.unlink()
        for name in files:
            (work / name).write_text("x", encoding="utf-8")
        out = self.dir / "mode-output"
        out.write_text("", encoding="utf-8")
        env = {"BRANCH": "feature", "GITHUB_OUTPUT": str(out)}
        return _bash(_run_body("generate", MODE_STEP), work, env), out.read_text(encoding="utf-8")

    def test_package_lock_present_means_convert(self) -> None:
        result, out = self.mode(("package.json", ".nvmrc", "package-lock.json"))
        self.assertEqual((result.returncode, out), (0, "mode=convert\n"))

    def test_both_lockfiles_means_convert(self) -> None:
        result, out = self.mode(("package.json", ".nvmrc", "package-lock.json", "pnpm-lock.yaml"))
        self.assertEqual((result.returncode, out), (0, "mode=convert\n"))

    def test_only_a_pnpm_lockfile_means_update(self) -> None:
        result, out = self.mode(("package.json", ".nvmrc", "pnpm-lock.yaml"))
        self.assertEqual((result.returncode, out), (0, "mode=update\n"))

    def test_neither_lockfile_is_refused(self) -> None:
        result, out = self.mode(("package.json", ".nvmrc"))
        self.assertEqual((result.returncode, out), (1, ""))

    def test_a_missing_package_json_is_refused(self) -> None:
        result, _ = self.mode((".nvmrc", "pnpm-lock.yaml"))
        self.assertEqual(result.returncode, 1)

    def resolve(self, workspace: str | None) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
        repo = self.dir / "repo"
        repo.mkdir(exist_ok=True)
        runner = self.dir / "runner"
        runner.mkdir(exist_ok=True)
        (repo / "package.json").write_text("{}", encoding="utf-8")
        (repo / "pnpm-lock.yaml").write_text("lock", encoding="utf-8")
        (repo / ".npmrc").write_text("@branchleft:registry=https://evil.example\n", encoding="utf-8")
        if workspace is not None:
            (repo / "pnpm-workspace.yaml").write_text(workspace, encoding="utf-8")
        bin_dir = self.dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        pnpm = bin_dir / "pnpm"
        pnpm.write_text(f"#!/bin/sh\necho \"$@\" > '{self.dir}/pnpm-args'\n", encoding="utf-8")
        pnpm.chmod(0o755)
        env = {"RUNNER_TEMP": str(runner), "NODE_AUTH_TOKEN": "x", "PATH": f"{bin_dir}:{os.environ['PATH']}"}
        result = _bash(_run_body("generate", RESOLVE_STEP), repo, env)
        return result, runner / "pnpm-lockfile-update"

    def test_the_isolated_directory_holds_only_the_allowed_inputs(self) -> None:
        result, work = self.resolve("allowBuilds:\n  protobufjs: false\nminimumReleaseAgeExclude:\n  - 'a@1'\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            sorted(p.name for p in work.iterdir()),
            [".npmrc", "package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"],
        )
        self.assertEqual((work / ".npmrc").read_text(encoding="utf-8"), "@branchleft:registry=https://npm.pkg.github.com\n")
        args = (self.dir / "pnpm-args").read_text(encoding="utf-8").strip()
        self.assertEqual(args, "install --lockfile-only --no-frozen-lockfile --ignore-scripts")

    def test_a_branch_without_a_workspace_file_resolves_without_one(self) -> None:
        result, work = self.resolve(None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((work / "pnpm-workspace.yaml").exists())

    def test_a_workspace_file_with_another_top_level_key_is_refused_before_pnpm_runs(self) -> None:
        result, work = self.resolve("allowBuilds:\n  protobufjs: false\nregistries:\n  default: https://evil.example\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("registries:", result.stdout)
        self.assertFalse((self.dir / "pnpm-args").exists())
        self.assertFalse((work / "pnpm-workspace.yaml").exists())

    def sweep(self, change: str | None) -> subprocess.CompletedProcess[str]:
        repo = self.dir / "sweep"
        repo.mkdir(exist_ok=True)
        # No GIT_* from the caller: under a git hook GIT_DIR and GIT_INDEX_FILE
        # would point these commands at the repository being committed.
        clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        isolated = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        env = {**clean, **isolated}
        ident = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
        (repo / "pnpm-lock.yaml").write_text("a", encoding="utf-8")
        (repo / "other.txt").write_text("a", encoding="utf-8")
        for command in (["init", "-q"], ["add", "."], [*ident, "commit", "-q", "-m", "y"]):
            subprocess.run(["git", *command], cwd=repo, env=env, check=True, capture_output=True)
        if change == "lock":
            (repo / "pnpm-lock.yaml").write_text("b", encoding="utf-8")
        elif change == "lock+other":
            (repo / "pnpm-lock.yaml").write_text("b", encoding="utf-8")
            (repo / "other.txt").write_text("b", encoding="utf-8")
        elif change == "lock+untracked":
            (repo / "pnpm-lock.yaml").write_text("b", encoding="utf-8")
            (repo / "stray.txt").write_text("b", encoding="utf-8")
        return _bash(_run_body("generate", SWEEP_STEP), repo, isolated)

    def test_only_the_lockfile_modified_passes(self) -> None:
        self.assertEqual(self.sweep("lock").returncode, 0)

    def test_nothing_modified_is_refused(self) -> None:
        self.assertEqual(self.sweep(None).returncode, 1)

    def test_another_modified_file_is_refused(self) -> None:
        result = self.sweep("lock+other")
        self.assertEqual(result.returncode, 1)
        self.assertIn("other.txt", result.stdout)

    def test_an_untracked_file_is_refused(self) -> None:
        self.assertEqual(self.sweep("lock+untracked").returncode, 1)


if __name__ == "__main__":
    unittest.main()
