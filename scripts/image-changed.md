# image-changed.py

Decides whether the image this stack pins is different from the one last
deployed. Run by the `Plan` job in `.github/workflows/infra-ci.yml` as
`python3 -I scripts/image-changed.py`, next to `plan-has-changes.py`, so the
`Deploy` job can be skipped, and its approval never asked for, when nothing
would change.

## Why a second check

The deploy job does two things: the Pulumi apply, and an SSH step that puts the
pinned image on the app host. The apply renders configuration and creates
nothing, so a push that changes only `imageRef` can show a Pulumi plan of
nothing but `same` and still be a real deploy. `plan-has-changes.py` reads the
plan, so on its own it could report `false` over an image that has not shipped.
This script reads the other half.

`plan-has-changes.py` and `test_plan_has_changes.py` are byte-for-byte copies
of the same files in `branchLeft/shared-infra` (`scripts/`, git blobs
`cb94741cbbf3f470820fb79ce12b11718bdfde8d` and
`abbe29a4f0b87d301839b3bec13f44a419b273c8`), and `plan-has-changes.md` is the
copy `branchLeft/ghost-platform` carries (git blob
`62061fb7e998569e14fad6fc1a165adfdf4f5df9`). Re-copy them deliberately, never
edit them here. The `.md` says "apply job" where this repo's job is `Deploy`,
and its headline says the apply is skipped: this workflow gives the script
files of its own for the verdict and the headline and never shows its
no-change headline, because the script knows only half of the answer.

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

1. List the runs of `infra-ci.yml` on `main`, event `push`, newest 30, once
   filtered to `status=success` and once unfiltered. Every entry in both must
   carry a numeric `id` and `run_number`, a 40-hex `head_sha`, a `status`, a
   `conclusion` and the event and branch asked for; one that does not is
   refused, so a listing that ignored its filter is not read.
2. Find the current run in the unfiltered listing, by `GITHUB_RUN_ID`, with the
   `run_number` the runner reports. Absent or different: `true`.
3. Any earlier run (a lower `run_number`) that is not `completed` is still
   waiting, queued or running and may yet deploy an older image after this one:
   `true`.
4. Any later run that succeeded has already put its own image on the host:
   `true`.
5. The baseline is the earlier run with the highest `run_number` among the
   successes. By induction its `head_sha` holds the pin that is on the host: its
   deploy job succeeded, or was skipped because nothing changed. Failed,
   rejected and cancelled runs are never read. None at all: `true`.
6. Read the stack config at that sha through the contents API and compare the
   `imageRef` value with the checkout's. Equal: `false`.

The `imageRef` line is read in one shape only: a non-comment line
`<project>:imageRef: <ref>` whose value is a plain scalar ending in a
`@sha256:` digest. Exactly one non-comment line may name the key. Zero, two, a
quoted value, a block scalar, a trailing comment or a non-UTF-8 file is `true`.

## What it does not catch

- A host image changed by hand is never re-asserted: this compares commits, not
  the host. Pulumi drift detection has the same limit.
- An earlier run older than the newest 30 that is still pending is not seen.
- A run whose records are gone (retention) leaves no baseline, which is `true`.
- Whether the Pulumi plan carries the image as an output change is a separate
  question, answered by `plan-has-changes.py`; this check does not depend on it.

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
`earlier-run-pending`, `later-run-succeeded`, `current-run-unlocated`,
`run-order-unknown`, `runs-unreadable`, `api-error`, `pin-line-count`,
`pin-line-shape`, `config-unreadable`, `baseline-unreadable`, `bad-argument`,
`bad-environment`, `internal-error`.

## Tests (`test_image_changed.py`, and `test_plan_job.py` for the wiring)

The script is run for real against a local HTTP server answering from
`testdata/image-changed/`. The ones that matter: an image-only change gives
`true` in both directions over an otherwise identical run; the only `false` is
equal pins in a quiet history; each unreadable input (no earlier success, an API
error on each of the three reads, a redirect, a reply that is not JSON, a run
order that cannot be established, a pending earlier run, a later success, a
missing, repeated or quoted pin line) gives `true` on its own.

`test_plan_job.py` runs the four deciding steps' own `run:` bodies in order under
bash, over an all-`same` preview and plan: an image-only change still gives
`has_changes=true`; a changed plan gives `true` with equal pins; a missing saved
plan or a missing verdict file gives `true`; only a clean plan with equal pins
gives `false`. `test_assert_environment_gated.py` fails if any job declaring the
`production` environment lacks the has_changes operand in its `if:`.
