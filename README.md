# ghost-platform-tenant-template

GitHub template repo for a tenant's infrastructure stack on the branchLeft Ghost
hosting platform. A run of the platform's provisioning flow generates a repo
from this template, substitutes its placeholders and opens a handover pull
request — one repo per tenant, never a shared roster.

## What this repo is, and isn't

- **`branchLeft/ghost-platform`** (public) holds the reusable, tenant-anonymous
  `GhostTenant` Pulumi *component*, published as
  `@branchleft/ghost-platform-tenant` on GitHub Packages — what a tenant's
  configuration looks like, with no tenant's name, hostname or credentials
  anywhere in it.
- **A repo generated from this template** holds one tenant's actual stack
  invocation — its own slug, hostname, UID, port and credentials.

One Pulumi program (`index.ts`), one stack, one tenant.

**This stack creates nothing.** The component declares no cloud resources,
because every durable thing a tenant uses already exists and is shared: the app
host and `db1` come from the estate's own stack, and object storage is an
account-level service. What is genuinely per-tenant is *configuration* — a
Compose stack carrying a runtime-isolation posture, a secrets file, a UID, two
volumes and a set of Ghost environment variables — and that is what this
renders. The stack is the versioned, reviewed, passphrase-wrapped record of it,
and its checkpoint is what the delete guard is meant to protect — see the
caveat under **Delete-guard preflight** for the half of that which does not
currently work.

So `pulumi up` here is not a deploy. Its useful products are two outputs an
operator places on the host by hand:

| Output | Goes to |
|---|---|
| `composeFile` | `/opt/branchleft/<slug>/compose.yml`, root-owned |
| `secretsEnvFile` (a Pulumi secret) | `/etc/branchleft/<slug>.env`, root-owned `0600` |
| `hostProvisioningCommand` | run as root on the app host, before the unit is enabled |
| `edgeRequestBodyMaxSize` | this tenant's site block in the edge's site registry, in `branchLeft/shared-infra` |
| `image` | read by the deploy job and piped to `branchleft-deploy` over this repo's slot key |

The only thing this repo's CI puts on a host is that last one: an image
reference. `compose.yml` and the secrets file are never written by an automated
path, because between them they *are* the runtime-isolation posture — a stack
that silently loses one line of it still starts, still serves, and has dropped a
boundary.

## No GCP

Nothing in a generated tenant repo touches Google: no service account, no
Workload Identity Federation, no KMS-wrapped secrets provider, no GCS state
bucket. State is a Hetzner Object Storage bucket reached through
`PULUMI_BACKEND_URL`, and this stack's secrets are wrapped by a passphrase
minted for this tenant alone.

The provider-enforced repository pin that WIF gave is **not replaced like for
like**. Its replacement is that every credential this repo holds is an
environment-scoped secret on `production`, readable only by the one job that
declares that environment.

## Placeholders

`scripts/generate-tenant-repo.py --slug <slug>` substitutes these `__LIKE_THIS__`
tokens when a tenant's repo is generated from this template. The provisioning
flow runs that script from the fresh clone rather than carrying its own copy of
the substitution list — which files carry a placeholder is a property of this
repo, and a duplicate list in another repository's workflow goes stale the first
time a placeholder is added to a new file, silently.

The script refuses if any placeholder survives anywhere in the tree, and
`scripts/test_generate_tenant_repo.py` runs the whole generation against a copy
of this repo's own tracked files on every push — so a placeholder added to an
unlisted file fails here rather than shipping into a tenant repo as a literal
token. It also refuses a reserved slug (`website`, `edge`, `db`, `monitoring`,
cross-checked against the installed component) and one too long for MySQL's
account-name limit, before a repository exists to clean up.

CI additionally refuses to type-check or deploy while either token remains
unsubstituted (`scripts/assert-placeholders-substituted.py`).

| Placeholder | File | What it becomes |
|---|---|---|
| `__TENANT_PULUMI_PROJECT__` | `Pulumi.yaml` | The Pulumi project name. Every tenant stack shares one state bucket and the object path derives from this, so a duplicate is a collision. |
| `__TENANT_NAME__` | `.github/workflows/infra-ci.yml`, `README.tenant.md` | The Pulumi stack name, equal to the `slug` stack config value. |

One file is swapped rather than substituted in place: provisioning renames
`README.tenant.md` over this `README.md` and then substitutes its placeholders,
so a generated repo's landing page describes that tenant's stack instead of
calling itself a template.

`scripts/assert-no-tenant-deletes.py` no longer carries a placeholder because it
no longer exists here — the guard ships inside
`@branchleft/ghost-platform-tenant` at `scripts/assert-no-tenant-deletes.py`, so
it travels with the component whose plan it judges rather than being a copy that
drifts from it.

## Stack config

`Pulumi.<slug>.yaml`, split by who writes it.

**Written by the provisioning flow**, plain values, reviewable in the handover
pull request's diff:

| Key | What it is |
|---|---|
| `slug` | This tenant's slug. Compose project, systemd instance, `/opt/branchleft` directory, MySQL database and account, both volume names. |
| `siteUrl` | `https://<hostname>` this tenant serves. |
| `uid` | This tenant's reserved UID on its app host. **Allocated against the host**, never derived — see below. |
| `appHostPrivateIp` | The app host's **private** address. Every published port binds this alone. Not the address CI connects to. |
| `hostPort` | This tenant's host-side port, distinct per tenant on that host. |
| `imageRef` | The image this tenant runs, digest-pinned. Refused at preview if it carries no `@sha256:`. |
| `databaseHost` | `db1`'s private address. |
| `databaseMaxUserConnections` | Optional. The cap applied on `db1`, recorded so it is visible here. |
| `mediaEndpoint`, `mediaRegion` | Object Storage addressing, platform-wide. The endpoint host and the region must name the same location, or the failure is a 403 that reads as a credential problem. **There is no `mediaBucket` and no `mediaPublicBaseUrl`**: this tenant's bucket is `branchleft-media-<slug>`, derived from the slug inside the component. The bucket is the only boundary between this tenant's media and another tenant's, so it is deliberately not something a stack can set. |
| `uploadCeilingMib`, `rssBudgetMib` | Optional. Left unset, the component's defaults apply. |

**Set by an operator** with `pulumi config set --secret`, in that same pull
request — never by the provisioning flow, because a `workflow_dispatch` input is
plaintext in the run's API response and its form:

| Key | Where the value comes from |
|---|---|
| `databasePassword` | Printed **once** by `db/provision/provision_tenant_db.py` on `db1`. Printed by nothing afterwards; a re-run leaves an existing password alone and says nothing about it. |
| `mediaAccessKeyId`, `mediaSecretAccessKey` | This tenant's Object Storage key pair, created in the Hetzner Cloud Console — no API mints one — and allowlisted by bucket policy to this tenant's bucket alone. Both are secret config, including the id: holding the pair together is what makes a rotation one edit rather than two. |

`uid` deserves its own note. It is host state: `provision_tenant_volume.py
--list-claims` on the app host reports which UIDs are already handed out, the
script refuses one another tenant holds, and it refuses changing a provisioned
tenant's UID because that is a data loss rather than an update. Nothing in a
config file can answer the allocation question for a host several tenant repos
deploy to.

## Optional mail config

Ordinary stack config keys, set by hand once, if and when a tenant needs
outbound mail. Omitting `mailHost` sends no `mail` block to the component at all
and the tenant boots exactly as it did without mail. Setting `mailHost` makes
the rest `require`d, so a half-configured block fails at preview.

| Key | Required once mail is enabled | Becomes |
|---|---|---|
| `mailHost` | — (this is the toggle) | `GhostTenantMailArgs.host` |
| `mailPort` | No — defaults to `587` | `GhostTenantMailArgs.port` |
| `mailUser` | Yes | `GhostTenantMailArgs.user` |
| `mailFrom` | Yes | `GhostTenantMailArgs.from` |
| `mailPassword` | Yes, as a secret | `GhostTenantMailArgs.password`, emitted into the secrets file |

## Optional bulk-email config

Same all-or-nothing shape.

| Key | Required once bulk email is enabled | Becomes |
|---|---|---|
| `bulkEmailBaseUrl` | — (this is the toggle) | `GhostTenantBulkEmailArgs.baseUrl` |
| `bulkEmailDomain` | Yes | `GhostTenantBulkEmailArgs.domain` |
| `bulkEmailApiKey` | Yes, as a secret | `GhostTenantBulkEmailArgs.apiKey` |

## Repo variables and environment secrets

| Name | Kind | What it is | Written by |
|---|---|---|---|
| `PULUMI_BACKEND_URL` | variable | The full `s3://…?endpoint=…&s3ForcePathStyle=true&region=…` state backend URL, not a bucket name. | provisioning |
| `APP_HOST_SSH_ADDRESS` | variable | The app host's **public** address. CI runs on GitHub-hosted runners and cannot reach the private one `appHostPrivateIp` names. | provisioning |
| `PULUMI_CONFIG_PASSPHRASE` | `production` env secret | This stack's own secrets passphrase, minted fresh at onboarding, never shared with another tenant or with the platform repo that created it. | provisioning |
| `PULUMI_ENCRYPTION_SALT` | `production` env secret | This stack's `encryptionsalt`, the `v1:`-prefixed value alone. | provisioning |
| `HETZNER_S3_ACCESS_KEY_ID` / `HETZNER_S3_SECRET_ACCESS_KEY` | `production` env secrets | Reach the state backend. Not scoped per stack — they reach every tenant stack in that bucket, which is why that bucket holds tenant stacks alone and never the estate's own. | provisioning |
| `APP_HOST_DEPLOY_KEY` | `production` env secret | This tenant's own ed25519 slot key. **Written by an operator, never by provisioning** — minting a host credential and writing it to a repository in one unattended run is a credential-creating credential, which the deploy design exists to not have. | operator |

**Environment secrets, never repository secrets.** A repository secret is
readable by any workflow run in the repo, including one added on a branch; an
environment secret is readable only by a job declaring that environment, which
here is the deploy job alone. On a public tenant repo the environment also
carries a required-reviewer rule; on a private one it cannot, which is a plan
tier limit rather than a misconfiguration — the scoping is then the whole of
what the environment buys, and it is still the property that matters.

`known_hosts` is committed, not a secret: it holds the app host's SSH **host**
public key, and publishing it is what pinning means. The deploy job refuses
while it carries no key line.

`npm ci` installs `@branchleft/ghost-platform-tenant` using the workflow run's
own `GITHUB_TOKEN`. The package is public on GitHub Packages, but that registry
rejects an unauthenticated request, so *any* valid token works — a generated
repo holds no long-lived package-read credential, and none is copied into it.

## Running this locally

```bash
git clone https://github.com/branchLeft/<generated-repo>.git
cd <generated-repo>
npm ci
npx tsc --noEmit          # type-check only, no credentials needed
```

`npm ci` needs a GitHub PAT with `read:packages` scope (see `.npmrc`).
`pulumi preview`/`up` additionally need this stack's passphrase, its salt and the
Object Storage credentials for the state backend — see `RUNBOOK-bootstrap.md`.

## The encryption salt is not committed

`branchLeft/standards` PUL-12 bans a committed `encryptionsalt`. The salt is an
offline verifier for the stack passphrase — whoever holds it can test candidates
at their own rate, with no state backend and no cloud IAM in the loop. Encrypted
`secure:` config values stay committed regardless: a ciphertext with no salt
beside it is not an oracle.

The provisioning flow moves the salt to `PULUMI_ENCRYPTION_SALT` and deletes the
line before it commits the stack config, and the deploy job appends it back to
the working copy for that job alone. To apply by hand from a checkout, append
your own held copy and do not commit it:

```bash
printf '\nencryptionsalt: %s\n' "$PULUMI_ENCRYPTION_SALT" >> Pulumi.<slug>.yaml
```

`scripts/assert-no-committed-pulumi-secrets.py` is the mechanical check, because
Pulumi writes the salt back into the file itself during an ordinary
`pulumi config set` and the diff then looks like what the command was asked to
do. It runs as a pre-commit hook and as CI's `Committed-secret guard` job, and
its module docstring lists the shapes it cannot see.

```bash
python3 scripts/assert-no-committed-pulumi-secrets.py --self-test
python3 scripts/assert-no-committed-pulumi-secrets.py --scan-tree .
python3 -m unittest discover -s scripts -p 'test_*.py'
```

**The guard job needs to be a required status check to block anything.** Outside
a ruleset it reports red and the merge goes through anyway. It is deliberately
not a job the deploy depends on: a salt already on `main` is already in every
clone, so refusing to apply would take the site down without taking the salt
back.

## Delete-guard preflight

The guard is `node_modules/@branchleft/ghost-platform-tenant/scripts/assert-no-tenant-deletes.py`,
shipped inside the component. CI runs it against a real `pulumi preview --json`
plan. It has two halves, and only one of them currently works.

**What it does refuse:** any `delete` or `replace` step in the plan. That
catches a slug change, which Pulumi renders as a delete and a create, and with
it the content volume rename that orphans this tenant's themes and settings on
the host under the old name.

**What it also refuses, from component `3.0.0` onward:** a change to any
identity field — `uid`, `stackName`, `contentVolume`, `adaptersVolume`,
`databaseName` or `appHostPrivateIp`. Each is a data migration rather than an
update, and each is now a real `update` step the guard compares rather than
something it silently fails to see.

> **This protection did not exist before `3.0.0`, and the history is worth
> keeping.** Under `2.0.0` and earlier, `GhostTenant` registered itself with
> empty inputs (`super(..., {}, opts)`), so a change to `uid` or
> `appHostPrivateIp` produced no step for `ghostPlatform:tenant:GhostTenant` in
> the plan at all, the guard's identity comparison matched zero steps, and it
> exited 0. That was reproduced rather than suspected: `uid` 30007 → 30099
> previewed clean, applied, and re-rendered `user: '30099:30099'` over a content
> volume owned `0700` by 30007. Fixed in
> [branchLeft/workspace#280](https://github.com/branchLeft/workspace/issues/280);
> a tenant repo pinned below `3.0.0` still has the gap.

**The preview above must keep `--show-sames` for this to work.** The `3.0.0`
guard refuses a plan carrying no step at all for the component, and Pulumi omits
an unchanged `ComponentResource` from `steps` without that flag — which is
indistinguishable from the component having stopped registering its identity.
Dropping the flag breaks every tenant change that touches no identity field.

```bash
GUARD=node_modules/@branchleft/ghost-platform-tenant/scripts/assert-no-tenant-deletes.py
python3 "$GUARD" --self-test
python3 "$GUARD" --verify-coverage node_modules/@branchleft/ghost-platform-tenant
```

`--verify-coverage` takes the package **root**: the script looks for
`dist/index.js` beneath what it is given, so pointing it at `dist` asks for
`dist/dist/index.js` and it reports a missing tree rather than a coverage
result.
