# generate-pnpm-lockfile.yml

Resolves `pnpm-lock.yaml` in CI with the workflow's own token, because the
private `@branchleft/*` GitHub Packages cannot be read from a workstation
without a credential. Dispatch only: `gh workflow run generate-pnpm-lockfile.yml --ref <ref> -f branch=<branch>`.
The `branch` input names the branch that is read and committed to; `--ref`
names the ref the workflow file itself is taken from.

## What both modes share

- The `validate` job runs first and the `generate` job needs it. It refuses
  `main`, `master`, a qualified ref (`refs/...`), a malformed name, and any
  branch a ruleset currently protects (a live read; an unreadable answer counts
  as protected).
- Resolution happens in a directory under `$RUNNER_TEMP`, never in the
  checkout, with a `.npmrc` the step writes itself. The branch's own `.npmrc`
  is never copied, so a branch cannot point the registry scope elsewhere.
- Install runs with `--ignore-scripts`.
- The commit is made with `createCommitOnBranch`, never `git commit` and
  `git push`, pinned to the checked-out head with `expectedHeadOid`, and the
  step reads the commit back and fails unless GitHub reports it verified.
- A commit made this way does not start `push` or `pull_request` runs. Close and
  reopen an open pull request to start its checks.

## Mode, chosen from the branch

| Branch has                                   | Mode    | Commit                                              |
| -------------------------------------------- | ------- | --------------------------------------------------- |
| `package-lock.json`                          | convert | `pnpm-lock.yaml` added, `package-lock.json` deleted |
| no `package-lock.json`, has `pnpm-lock.yaml` | update  | `pnpm-lock.yaml` only                               |
| neither                                      | refused | none                                                |

`package.json` and `.nvmrc` must exist in both modes. When both lockfiles
exist, convert wins.

## Convert mode

`pnpm import` then `pnpm install --lockfile-only`, a comparison of the resolved
version set of every package both lockfiles name, then a swap. It refuses any
working-tree change beyond the swap.

## Update mode

For a branch that changed a dependency specifier in `package.json` and left
`pnpm-lock.yaml` behind, so `pnpm install --frozen-lockfile` fails with
`ERR_PNPM_OUTDATED_LOCKFILE`.

The isolated directory receives `package.json`, `pnpm-lock.yaml`, a `.npmrc`
written by the step and, if present, `pnpm-workspace.yaml`. The workspace file
holds resolution settings, including the release-age exclusions a package
published in the last day needs, so it is needed. It is copied only when its
top-level keys are `allowBuilds` and `minimumReleaseAgeExclude`; any other key is
refused before pnpm runs. Resolution is `pnpm install --lockfile-only
--no-frozen-lockfile --ignore-scripts`; the explicit flag is there because CI
makes pnpm default to a frozen lockfile.

What update mode refuses:

1. The re-resolved lockfile is identical to the branch's.
2. The lockfile version or `settings:` block differs.
3. The lockfile has an importer other than the root `.`.
4. The new lockfile's importer specifiers differ from `package.json`.
5. No `package.json` specifier differs from the old lockfile's (nothing
   explains a change).
6. A direct dependency whose specifier did not change resolved to a different
   version, or its dependency tree gained or lost any package.
7. A package was added or removed that no bumped direct dependency reaches.
8. A package kept its version but its `packages:` entry (resolution, integrity,
   engines) changed.
9. Any working-tree change other than a modified `pnpm-lock.yaml`.

Items 2 to 8 are one Python gate, inline in the workflow so a branch that lacks
the script is still checked by it. The tree is walked from the `snapshots:`
section, so a transitive package that moves is attributed to the direct
dependency that reaches it. A bump that makes pnpm re-share a transitive package
with an unbumped dependency is therefore refused rather than accepted; resolve
that one by hand.

## Tests

`scripts/test_generate_pnpm_lockfile.py` cuts each step out of the workflow and
runs it: the gate against built and pnpm-written lockfile pairs, the mode
choice, the workspace allow-list and the exact-change check under bash, and the
branch refusals with a stub `gh`. It runs in the credential-free job that runs
the other `scripts/` tests.
