# assert-environment-gated.py

Exits 0 only if a GitHub environment really gates its deploy job. Run by the
`Deploy` job in `.github/workflows/infra-ci.yml` as its first step after
checkout, before any environment secret is read.

**This file is a byte-for-byte copy**, not a source of truth. Origin:
`branchLeft/ghost-platform`, `infra/scripts/assert-environment-gated.py`, as at
commit `2c7db4dac511d628a5deaff145c97deb530782c3` (git blob
`c013e43b746c879b2836eb7b60e0627dae778009`). It is copied in rather than
fetched at run time: a run-time fetch would let a change to another repository
rewrite the check that guards this deploy. Re-copy it deliberately, and update
the pin in `test_assert_environment_gated.py` in the same change.

## What counts as gated

Either of:

- `GET environments/production` has a `protection_rules[]` entry with
  `type == "required_reviewers"`; or
- `GET environments/production/deployment_protection_rules` has a
  `custom_deployment_protection_rules[]` entry with `enabled == true`,
  `app.id == 5090756` and `app.slug == "branchleft-reviewer"` (both must match).

A custom deployment protection rule is not listed in `protection_rules[]`, so
the second endpoint has to be read as well. Either read failing, a body that is
not JSON, or an error body is **not gated**: the step fails closed.

## What it does not do

It does not decide who approves. A required reviewer who approves anything
satisfies it. It only stops the job from running ungated, which is what happens
if the environment's last rule is removed, or the reviewer App is suspended or
uninstalled, and GitHub then lets a job run with no pause and no message.

A private repository on a plan without environment protection cannot carry
either rule, so the step refuses its deploys. That is deliberate: the refusal is
the safe default, and lifting it for such a repository is a decision, not a
default.

## Tests (`test_assert_environment_gated.py`)

The pin test fails if the script's sha256 moves. The structural tests fail if
the step is removed, is not first after checkout, follows a step that reads a
secret, names a different environment from the job, or if the job's permissions
widen. The stub-`gh` tests run the step's own `run:` body: passes with a
required reviewer or an enabled rule; refuses with no rule, a disabled rule,
another app's rule, an unreadable endpoint, a malformed body and an error body.
