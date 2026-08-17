# ghost-platform-tenant-template

GitHub template repo for a tenant's private infrastructure stack on the
branchLeft Ghost hosting platform. A run of the platform's provisioning flow
generates a repo from this template, substitutes its placeholders and
provisions everything it needs -- one private repo per tenant, never a shared
roster.

## What this repo is, and isn't

Per the platform's decided repo topology (documented in the platform's
private architecture doc set):

- **`branchLeft/ghost-platform`** (public) holds the reusable, tenant-anonymous
  `GhostTenant` Pulumi *component*, published as `@branchleft/ghost-platform-tenant`
  on GitHub Packages -- what a tenant's infrastructure looks like, with no
  tenant's name, hostname, or config anywhere in it.
- **A repo generated from this template** holds one tenant's actual Pulumi
  *stack invocation* -- its own name, hostname, and config. A hostname +
  Cloud Run service name pair is that tenant's identity; keeping it in a
  private, per-tenant repo (rather than a shared roster) is why Layer 1's
  private side is one repo per tenant.

One Pulumi program (`index.ts`), one stack, one tenant.

**This repo declares no identity.** The deployer service account, its Workload
Identity pool and provider, its project roles and this tenant's Pulumi state
bucket are all created by the platform's provisioning flow and live in that
stack's state -- a Pulumi program cannot create the identity it runs as, and
the roles needed to try are the ones a deploy identity must never hold. What
arrives here instead is four stack config values (the database instance
connection name, the tenant image repository path, the media bucket URL, the
deployer service account email), four repo variables and one repo secret, all
written at provisioning time.

## Placeholders

The provisioning flow substitutes these `__LIKE_THIS__` tokens when it
generates a tenant's repo from this template. Do not fill them in by hand
except for local testing.

CI refuses to type-check or deploy while either remains unsubstituted
(`scripts/assert-placeholders-substituted.py`). `__TENANT_NAME__` is validated
downstream anyway -- `--stack` errors with "stack not found" -- but nothing
downstream validates the Pulumi project name, so this check exists
specifically to make that one fail closed too.

| Placeholder | File | What it becomes |
|---|---|---|
| `__TENANT_PULUMI_PROJECT__` | `Pulumi.yaml` | The Pulumi project name, which the state object path is derived from. |
| `__TENANT_NAME__` | `.github/workflows/infra-ci.yml`, `scripts/assert-no-tenant-deletes.py`, `README.tenant.md` | The Pulumi stack name, equal to the `tenantName` stack config value. |

One file is swapped rather than substituted in place: provisioning renames
`README.tenant.md` over this `README.md` and then substitutes its
placeholders, so a generated repo's landing page describes that tenant's
stack instead of calling itself a template. A generated repo therefore does
not carry this file; its README links back here for the optional config
tables below.

Three placeholders are gone as of the move to provisioned identity --
`__TENANT_GITHUB_REPO__`, `__TENANT_WORKLOAD_IDENTITY_POOL_ID__` and
`__TENANT_DEPLOYER_SA_ID__` all named resources this repo no longer declares.

## Optional mail config

Unlike the placeholders above, these are ordinary Pulumi stack config keys in
`Pulumi.<tenant-name>.yaml` -- set by hand, once, if and when a tenant needs
outbound mail. Omitting `mailHost` (the default) sends no `mail` block to
`GhostTenant` at all, and the tenant boots exactly as it did before mail
existed. Setting `mailHost` makes the rest of the row `require`d.

| Key | Required once mail is enabled | What it becomes |
|---|---|---|
| `mailHost` | -- (this is the toggle) | `GhostTenantMailArgs.smtpHost` |
| `mailPort` | No -- defaults to `'587'` | `GhostTenantMailArgs.smtpPort` |
| `mailUser` | Yes | `GhostTenantMailArgs.smtpUser` |
| `mailFrom` | Yes | `GhostTenantMailArgs.from` |
| `mailSmtpPassword` | Yes, as a secret (`pulumi config set --secret`) | `GhostTenantMailArgs.smtpPassword`, routed to Secret Manager by the component |

## Optional bulk-email config

Same all-or-nothing shape as mail above, in `Pulumi.<tenant-name>.yaml`.
Omitting `bulkEmailBaseUrl` (the default) sends no `bulkEmail` block to
`GhostTenant` at all. Setting `bulkEmailBaseUrl` makes the rest of the row
`require`d.

| Key | Required once bulk email is enabled | What it becomes |
|---|---|---|
| `bulkEmailBaseUrl` | -- (this is the toggle) | `GhostTenantBulkEmailArgs.baseUrl` |
| `bulkEmailDomain` | Yes | `GhostTenantBulkEmailArgs.domain` |
| `bulkEmailApiKey` | Yes, as a secret (`pulumi config set --secret`) | `GhostTenantBulkEmailArgs.apiKey`, routed to Secret Manager by the component |

## Repo variables and secret

Written by the provisioning flow, not by hand:

| Name | Kind | What it is |
|---|---|---|
| `GCP_PROJECT_ID` | variable | The GCP project. |
| `GCP_DEPLOYER_SA_EMAIL` | variable | The deployer service account CI impersonates. |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | variable | The provider CI federates through. |
| `PULUMI_STATE_BUCKET` | variable | This tenant's own Pulumi state bucket, without the `gs://` scheme. |
| `PULUMI_CONFIG_PASSPHRASE` | secret | This tenant's own Pulumi secrets passphrase, minted fresh at onboarding and unique to this repo -- never shared with another tenant, and never the platform repo's own. Without it CI cannot decrypt this stack's checkpoint. |

`npm ci` installs `@branchleft/ghost-platform-tenant` using the workflow
run's own `GITHUB_TOKEN` -- the package is public on GitHub Packages, but
that registry still rejects an unauthenticated request, so *any* valid
token works. No long-lived package-read credential is held by this repo or
copied into it.

## Running this locally

```bash
git clone https://github.com/branchLeft/<generated-repo>.git
cd <generated-repo>
npm ci
npx tsc --noEmit          # type-check only, no credentials needed
```

`npm ci` needs a GitHub PAT with `read:packages` scope to install
`@branchleft/ghost-platform-tenant` (see `.npmrc`). `pulumi preview`/`up` need
this tenant's own state bucket and a GCP identity with access to it -- see
`RUNBOOK-bootstrap.md`.

## Delete-guard preflight

`scripts/assert-no-tenant-deletes.py` is CI's preflight against a real
`pulumi preview --json` plan, refusing to apply anything that would destroy
the tenant's service account, database, DB user, Cloud Run service or media
HMAC key. Run its self-test any time:

```bash
python3 scripts/assert-no-tenant-deletes.py --self-test
python3 scripts/assert-no-tenant-deletes.py --verify-coverage node_modules/@branchleft/ghost-platform-tenant/dist
```

See the script's own module docstring for what it can and can't prove.
