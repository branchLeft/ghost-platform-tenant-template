"""Holds the package-attestation job in infra-ci.yml to its shape.

The job calls the shared `verify-package-attestations` action. What must not
drift: the job exists and calls that action once; the call is pinned by a full
commit sha with a `# vX.Y.Z` comment; and the job's permissions are exactly
`contents: read` and `packages: read`. The workflow is read as text (stdlib
only, no YAML parser), and `problems()` is the one check, run on the real file
and, in the Sabotage class, on broken copies of it, which must each be refused.
"""

from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "infra-ci.yml"

JOB = "verify-package-attestations"
ACTION = "branchLeft/github-workflows/.github/actions/verify-package-attestations"
PERMISSIONS = {"contents": "read", "packages": "read"}

USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(?:\s+#\s*(\S.*))?$")
PINNED = re.compile(rf"^{re.escape(ACTION)}@[0-9a-f]{{40}}$")
VERSION_COMMENT = re.compile(r"^v\d+\.\d+\.\d+$")


def job_lines(text: str, job: str) -> list[str] | None:
    """The lines of one job under `jobs:`, or None when it is absent."""
    lines = text.splitlines()
    try:
        start = lines.index(f"  {job}:")
    except ValueError:
        return None
    end = len(lines)
    for number in range(start + 1, len(lines)):
        line = lines[number]
        if line.strip() and not line.startswith("   ") and not line.lstrip().startswith("#"):
            end = number
            break
    return lines[start + 1 : end]


def permissions_of(body: list[str]) -> dict[str, str] | None:
    """The job-level `permissions:` mapping (indent 4), or None when absent."""
    for number, line in enumerate(body):
        if line == "    permissions:":
            found: dict[str, str] = {}
            for entry in body[number + 1 :]:
                if not entry.strip() or entry.lstrip().startswith("#"):
                    continue
                if not entry.startswith("      "):
                    break
                key, _, value = entry.strip().partition(":")
                found[key.strip()] = value.strip()
            return found
    return None


def problems(text: str) -> list[str]:
    body = job_lines(text, JOB)
    if body is None:
        return [f"job {JOB} is missing from infra-ci.yml"]
    found: list[str] = []
    calls = [m for m in (USES.match(line) for line in body) if m and m.group(1).startswith(ACTION)]
    if len(calls) != 1:
        found.append(f"{JOB} must call {ACTION} exactly once, found {len(calls)} calls")
    for call in calls:
        reference, comment = call.group(1), (call.group(2) or "").strip()
        if not PINNED.match(reference):
            found.append(f"the call is not pinned by a full commit sha: {reference}")
        if not VERSION_COMMENT.match(comment):
            found.append(f"the call has no `# vX.Y.Z` comment naming the release: {comment!r}")
    permissions = permissions_of(body)
    if permissions != PERMISSIONS:
        found.append(f"{JOB} permissions must be exactly {PERMISSIONS}, found {permissions}")
    return found


class AttestationJob(unittest.TestCase):
    def test_the_real_workflow_is_clean(self) -> None:
        self.assertEqual(problems(WORKFLOW.read_text(encoding="utf-8")), [])

    def test_the_job_is_bounded(self) -> None:
        body = job_lines(WORKFLOW.read_text(encoding="utf-8"), JOB)
        assert body is not None
        self.assertIn("    timeout-minutes: 10", body)

    def test_the_job_is_not_a_needs_of_plan_or_deploy_by_accident(self) -> None:
        """No existing job's behaviour changed: gating on this job is a separate,
        deliberate edit, which would also edit this test."""
        text = WORKFLOW.read_text(encoding="utf-8")
        for job in ("plan", "deploy"):
            body = job_lines(text, job)
            assert body is not None
            needs = [line for line in body if line.startswith("    needs:")]
            self.assertEqual(len(needs), 1)
            self.assertNotIn(JOB, needs[0])


class Sabotage(unittest.TestCase):
    """Each control broken in a copy of the real file must be refused."""

    def real(self) -> str:
        return WORKFLOW.read_text(encoding="utf-8")

    def refused(self, text: str, fragment: str) -> None:
        found = problems(text)
        self.assertTrue(any(fragment in item for item in found), f"{fragment!r} not in {found}")

    def test_the_job_removed_is_refused(self) -> None:
        text = self.real().replace(f"  {JOB}:\n", "  renamed-away:\n", 1)
        self.refused(text, "is missing")

    def test_the_step_removed_is_refused(self) -> None:
        text = re.sub(rf"\n {{8}}uses: {re.escape(ACTION)}@\S+ # \S+", "\n        run: true", self.real())
        self.refused(text, "exactly once")

    def test_a_tag_pin_is_refused(self) -> None:
        text = re.sub(rf"({re.escape(ACTION)})@[0-9a-f]{{40}}", r"\1@v1.2.1", self.real())
        self.refused(text, "not pinned by a full commit sha")

    def test_a_missing_version_comment_is_refused(self) -> None:
        text = re.sub(rf"({re.escape(ACTION)}@[0-9a-f]{{40}}) # v\S+", r"\1", self.real())
        self.refused(text, "no `# vX.Y.Z` comment")

    def test_an_extra_permission_is_refused(self) -> None:
        body = job_lines(self.real(), JOB)
        assert body is not None
        old = "\n".join(body)
        new = old.replace("      packages: read", "      packages: read\n      id-token: write", 1)
        self.refused(self.real().replace(old, new, 1), "permissions must be exactly")

    def test_a_wider_permission_is_refused(self) -> None:
        body = job_lines(self.real(), JOB)
        assert body is not None
        old = "\n".join(body)
        new = old.replace("      packages: read", "      packages: write", 1)
        self.refused(self.real().replace(old, new, 1), "permissions must be exactly")

    def test_a_missing_permission_is_refused(self) -> None:
        body = job_lines(self.real(), JOB)
        assert body is not None
        old = "\n".join(body)
        new = old.replace("      packages: read\n", "", 1)
        self.refused(self.real().replace(old, new, 1), "permissions must be exactly")

    def test_a_job_with_no_permissions_block_is_refused(self) -> None:
        body = job_lines(self.real(), JOB)
        assert body is not None
        old = "\n".join(body)
        new = old.replace("    permissions:\n      contents: read\n      packages: read\n", "", 1)
        self.refused(self.real().replace(old, new, 1), "permissions must be exactly")


if __name__ == "__main__":
    unittest.main()
