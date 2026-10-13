# summarise-plan.py

Prints a tenant stack's `pulumi preview --json` plan so a person, or the
deploy-approval script, can read it before approving, and refuses a plan it
cannot read or that deletes or replaces anything.

## What it prints

One line the deploy-approval script parses out of the plan job's log, then one
line per step that is not `same`:

    OK: no protected Hetzner resource is destroyed by this plan (N steps checked, changeSummary={'same': 3, 'update': 1}).
    Steps other than same: 1
      update ghost:tenant:GhostTenant name

The first line is word for word the shape `ci_deploy_approve.py` in
`branchLeft/workspace` matches (its `PLAN_LINE_RE`), written by the estate's
own delete guard for the other stacks. The tenant stack's component guard
prints nothing on success, so this script writes the line. The words "protected
Hetzner resource" are the approver's, not a claim about this stack, which
creates no Hetzner resource; what the line asserts here is only that no step is
a delete or a replace. `test_summarise_plan.py` holds a copy of that pattern
and checks the line against it. The line appears exactly once, which is what the
approver requires, so no other output of the plan job may carry the marker word
`changeSummary` followed by an equals sign.

## What it never prints

A step's resource type and name only. Not the property values, not the stack
or project from the URN. This stack's outputs include a Pulumi secret, and on a
public tenant repo a job log is public. Anything outside `[A-Za-z0-9_.:$/@+-]`
in a type, a name or an operation becomes `?`, so a hostile name cannot start a
line with `::` (a runner command) or forge the summary line.

## Exit status

- 0: readable, and no step's operation contains `delete` or `replace`.
- 1: a delete or a replace. No OK line is printed.
- 2: the plan could not be read, had no steps, or was malformed. A plan that
  cannot be read is never treated as an empty one.

A `create` is printed, not refused here: the approver refuses a plan that
creates or imports a resource, and counts what it reads in the line.

## Usage

    summarise-plan.py PREVIEW_JSON
    summarise-plan.py --self-test

## Tests (`test_summarise_plan.py`)

The ones that matter: the printed line matches the approver's pattern once and
only once; each delete/replace operation spelling refuses; an unreadable plan
exits 2 and prints nothing to stdout; a sentinel property value never reaches
the log; a hostile resource name forges neither a log command nor a second
marker.
