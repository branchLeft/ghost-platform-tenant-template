# __TENANT_NAME__ — Ghost platform tenant stack

The infrastructure repo for one tenant of the branchLeft Ghost hosting
platform: stack `__TENANT_NAME__`, generated from
[`ghost-platform-tenant-template`](https://github.com/branchLeft/ghost-platform-tenant-template).

One Pulumi program (`index.ts`), one stack, one tenant. The reusable,
tenant-anonymous `GhostTenant` component lives in the public
[`ghost-platform`](https://github.com/branchLeft/ghost-platform) repo, published
as `@branchleft/ghost-platform-tenant`; this repo holds only this tenant's stack
invocation — its slug, hostname, UID, port and credentials.

**A hostname plus a slug is this tenant's identity.** Never carry either into a
public repo, an issue, or a pull-request description outside this repo.

## What this stack does, and does not

**It creates nothing.** Everything durable this tenant uses already exists and
is shared: the app host, `db1`, the Object Storage bucket. What is per-tenant is
*configuration*, and this stack is the versioned, passphrase-wrapped record of
it. `pulumi up` renders two things an operator places on the host by hand:

```bash
pulumi stack output composeFile                      # /opt/branchleft/__TENANT_NAME__/compose.yml
pulumi stack output --show-secrets secretsEnvFile    # /etc/branchleft/__TENANT_NAME__.env, 0600
```

Both are root-owned and operator-written. No automated path may write either:
between them they are the runtime-isolation posture, and a stack that quietly
loses one line of it still starts, still serves, and has dropped a boundary.

**The only thing CI puts on the host is an image reference.** The deploy job
pipes `imageRef` to `branchleft-deploy` over this repo's own slot key, which
writes `/etc/branchleft/__TENANT_NAME__.image.env` and restarts
`branchleft-compose@__TENANT_NAME__`. That key's `authorized_keys` entry carries
a forced command naming this stack, written by root — so it cannot address any
other tenant on that host, because there is no caller-supplied stack name
anywhere on the path.

## Day-to-day

- CI (`.github/workflows/infra-ci.yml`) type-checks every pull request, and on
  `main` applies the `__TENANT_NAME__` stack and then deploys its pinned image.
  Both jobs refuse to run while a template placeholder survives, and the deploy
  job's delete-guard preflight aborts any plan carrying a `delete` or `replace`
  step. Its other half does not currently fire — see the `uid` bullet below.
- Changes are pull requests against `Pulumi.__TENANT_NAME__.yaml` and
  `index.ts`. Secret config is encrypted with this repo's own passphrase — set
  it with `pulumi config set --secret` from a checkout, with
  `PULUMI_CONFIG_PASSPHRASE` exported locally, never by pasting plaintext into
  the file.
- **Changing the image** is a one-line change to `imageRef`, digest-pinned. A
  tag is refused at preview: a stack deployed by tag has no answer to "what is
  running", and a restart months later can silently change it.
- **Changing `uid` or `appHostPrivateIp` is NOT currently refused by anything**,
  and both orphan live data rather than updating it: the container starts under
  a UID that cannot read its own `0700` content volume. The delete guard's
  identity half matches no steps, because the component registers empty inputs
  and so never appears in the plan — reproduced against the published component,
  tracked as
  [branchLeft/workspace#280](https://github.com/branchLeft/workspace/issues/280).
  Treat both as fields to change only with a migration in front of them, and do
  not rely on CI to stop you. Changing the **slug** *is* refused, because Pulumi
  renders that as a delete and a create.
- **`edgeRequestBodyMaxSize`** has to reach this tenant's site block in the
  edge's site registry in `branchLeft/shared-infra`. It is derived from the same
  input as the container's `/tmp` ceiling so the two cannot disagree; setting it
  there by hand to a different number defeats that.
- This stack's `encryptionsalt` lives in the `PULUMI_ENCRYPTION_SALT`
  environment secret, not in the committed config. The deploy job appends it to
  the working copy for that job alone. To run `pulumi` locally, append your own
  held copy and do not commit it:
  `printf '\nencryptionsalt: %s\n' "$PULUMI_ENCRYPTION_SALT" >> Pulumi.__TENANT_NAME__.yaml`
- `scripts/assert-no-committed-pulumi-secrets.py` fails a commit, and CI's
  `Committed-secret guard` job fails a pull request, that puts a salt back.

Bootstrap record, the host-side steps and the teardown order:
`RUNBOOK-bootstrap.md`.
