# assert-plan-environment-main-only.py

Exits 0 only if a GitHub environment can be used by the branch `main` alone.

The plan job reads the state credentials, the stack passphrase and the salt
from the `production-plan` environment, and that environment has no reviewer
(a reviewer would stop the job before the plan exists). What stands in for the
reviewer is a deployment branch policy naming `main` and nothing else. An
environment with no policy hands its secrets to a job from any branch, and
says nothing, so the plan job reads the policy first and refuses.

## What passes

`deployment_branch_policy` set with `custom_branch_policies: true` and
`protected_branches: false`, and exactly one custom policy, named `main`, of
type `branch` (or with no type given).

## What fails (all exit 1)

No policy; protected-branches mode; a wildcard (`*`, `ma*`); a tag named
`main`; more than one policy; none; either API response unreadable, not JSON,
or an error body. One failed read fails the guard.

## API shapes

Read from GitHub's REST documentation for `GET /repos/{owner}/{repo}/environments/{name}`
(`deployment_branch_policy`) and
`GET /repos/{owner}/{repo}/environments/{name}/deployment-branch-policies`
(`branch_policies[].name`, `.type`). They are NOT recorded from a live
response, unlike `assert-environment-gated.py`'s: the first real run on a
tenant repo is the check, and a shape mismatch fails closed (the plan job
fails, so the deploy job never starts).

## Usage

    assert-plan-environment-main-only.py ENV_JSON POLICIES_JSON
    assert-plan-environment-main-only.py --self-test

## Tests (`test_plan_job.py`, `TheBranchPolicyGuard`)

Each failing shape above, a missing file, the self-test, and that the workflow
step reads the same environment name the job declares.
