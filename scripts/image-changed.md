# image-changed.py

Decides whether the image this stack pins is different from the one last
deployed. Run by the `Plan` job in `.github/workflows/infra-ci.yml` as
`python3 -I scripts/image-changed.py`, next to the `plan-has-changes` action, so the
`Deploy` job can be skipped, and its approval never asked for, when nothing
would change.

## Why a second check

The deploy job does two things: the Pulumi apply, and an SSH step that puts the
pinned image on the app host. The apply renders configuration and creates
nothing, so a push that changes only `imageRef` can show a Pulumi plan of
nothing but `same` and still be a real deploy. The `plan-has-changes` action reads the
plan, so on its own it could report `false` over an image that has not shipped.
This script reads the other half.

The plan verdict is the shared `plan-has-changes` action in
`branchLeft/github-workflows`, pinned by commit; this repo keeps no copy of it.
Re-pin deliberately, never edit a copy here. The workflow runs the action with
`write-summary: 'false'` and writes the one headline itself, because the action
knows only half of the answer.

The plan job's `has_changes` is `false` only when both say no change. The step
that combines them, `Decide whether the deploy is needed`, is the one writer of
that output and of the job summary; neither half writes either.

## The rule

Only a pin proved equal to the last deployed one is `false`. Everything else is
`true`: an unreadable input, an unexpected shape, an error of any kind. A wrong
`true` costs one approval click. A wrong `false` would silently not ship an
image.

"Last deployed" is read from the workflow-run history, because the plan job
holds no host key and no write scope (it has `actions: read` and
`contents: read`, and gains nothing):

1. List the runs of `infra-ci.yml` on `main`, event `push`, newest 30: filtered
   to `status=success`, unfiltered, and once each for `status=waiting`,
   `queued` and `in_progress`. Every entry must carry a numeric `id` and
   `run_number`, a 40-hex `head_sha`, a `status`, a `conclusion` and the event
   and branch asked for, and each listing a `total_count`; an entry that does
   not match its own filter is refused, so a listing that ignored its filter is
   not read.
2. Find the current run in the unfiltered listing, by `GITHUB_RUN_ID`, with the
   `run_number` the runner reports. Absent or different: `true`.
3. Any earlier run (a lower `run_number`) in the waiting, queued or in-progress
   listings, or not `completed` in the unfiltered one, may yet deploy an older
   image after this one: `true`. These are asked for by status, so a pending run
   older than the newest 30 is seen. A pending listing cut short (its
   `total_count` above the entries returned) is `true`, as is an API error on
   any of them.
4. Any later run that succeeded has already put its own image on the host:
   `true`.
5. The baseline is the earlier run with the highest `run_number` among the
   successes. None at all: `true`. Failed, cancelled and stopped runs are never
   the baseline, because only a success proves the host got its image.
6. Read the stack config at the baseline's sha through the contents API and
   compare the `imageRef` value with the checkout's. Different: `true`.
7. A deploy that failed or was stopped after the baseline can leave the host
   between a pin write and its restart, so it may hold that run's pin and not
   the baseline's. Every finished run after the baseline, and before this one,
   whose conclusion is not `success` (failure, cancelled, timed out, action
   required, anything else) must hold the current pin too, read the same way: a
   different pin, or a config that cannot be read, is `true`. For that to be
   complete the unfiltered window must reach back to the baseline; if it does
   not, `true`. Only then, all equal: `false`.

The `imageRef` line is read in one shape only: a non-comment line
`<project>:imageRef: <ref>` whose value is a plain scalar ending in a
`@sha256:` digest. Exactly one non-comment line may name the key. Zero, two, a
quoted value, a block scalar, a trailing comment or a non-UTF-8 file is `true`.

## What it does not catch

- A host image changed by hand is never re-asserted: this compares commits, not
  the host. Pulumi drift detection has the same limit.
- A history with more than 30 runs since the last success is `true`
  (`history-window`): a `false` needs every run after the baseline in view.
- A failed run since the baseline asks for the approval even if it stopped
  before it touched the host (a type-check failure, say): this reads the pin of
  the run, not how far it got. The next success moves the baseline past it.
- A run whose records are gone (retention) leaves no baseline, which is `true`.
- Whether the Pulumi plan carries the image as an output change is a separate
  question, answered by the `plan-has-changes` action; this check does not depend on it.

## Fail closed, and what leaves the machine

Every exception, including `BaseException`, is `true`. A redirect is an error,
not followed: the token goes to the API host and nowhere else, in a header
never in a URL, and only over `https` (plain `http` to a loopback address is
accepted so the tests can run a local server). Nothing is printed but a reason
code and the baseline run id: no pin, no config content, no token.

## Usage

    image-changed.py --workflow infra-ci.yml --config <stack config file> --github-output <file>

Reads `GITHUB_TOKEN`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID`,
`GITHUB_RUN_NUMBER` and `GITHUB_API_URL` (default `https://api.github.com`).
Appends two lines to the output file, `image_changed=<true|false>` and
`reason=<code>`, and exits 1 only if that file cannot be written.

Reason codes: `pin-equal`, `pin-differs`, `no-earlier-successful-run`,
`earlier-run-pending`, `pending-listing-truncated`, `later-run-succeeded`,
`history-window`, `intervening-run-pin-differs`, `intervening-run-unreadable`,
`current-run-unlocated`,
`run-order-unknown`, `runs-unreadable`, `api-error`, `pin-line-count`,
`pin-line-shape`, `config-unreadable`, `baseline-unreadable`, `bad-argument`,
`bad-environment`, `internal-error`.

## Tests (`test_image_changed.py`, and `test_plan_job.py` for the wiring)

The script is run for real against a local HTTP server answering from
`testdata/image-changed/`. The ones that matter: an image-only change gives
`true` in both directions over an otherwise identical run; the only `false` is
equal pins in a quiet history; each unreadable input (no earlier success, an API
error on each listing and the contents read, a redirect, a reply that is not
JSON, a run order that cannot be established, a pending earlier run (including
one outside a full 30-entry window, for each of the three statuses), a pending
listing cut short, a later success, a missing, repeated or quoted pin line) gives
`true` on its own. A failed, cancelled, timed-out or action-required run between
two successes gives `true` when its pin differs (both directions), `false` only
when every such run holds the current pin, and `true` when one's config cannot be
read.

`test_plan_job.py` runs the four deciding steps' own `run:` bodies in order under
bash (the plan save against a stub `pulumi`, the image check against the local
API), over an all-`same` preview and plan: an image-only change still gives
`has_changes=true`; a changed plan gives `true` with equal pins; a missing saved
plan or a missing verdict file gives `true`; only a clean plan with equal pins
gives `false`. It also asserts that no step but the plan save and the parser
names the saved plan, which holds secrets-provider ciphertext.
`test_assert_environment_gated.py` fails if any job declaring the `production`
environment lacks the has_changes operand in its `if:`.
