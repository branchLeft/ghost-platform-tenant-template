# __TENANT_NAME__ — Ghost platform tenant stack

The private infrastructure repo for one tenant of the branchLeft Ghost
hosting platform: stack `__TENANT_NAME__`, generated from
[`ghost-platform-tenant-template`](https://github.com/branchLeft/ghost-platform-tenant-template).

One Pulumi program (`index.ts`), one stack, one tenant. The reusable,
tenant-anonymous `GhostTenant` component lives in the public
[`ghost-platform`](https://github.com/branchLeft/ghost-platform) repo,
published as `@branchleft/ghost-platform-tenant`; this repo holds only this
tenant's stack invocation -- its name, hostname and config.

**Private, and a tenant roster of one.** A hostname plus Cloud Run service
name is this tenant's identity. Never carry either into a public repo, an
issue, or a PR description outside this repo.

**This repo declares no identity.** The deployer service account, its
Workload Identity pool and provider, its project roles and this tenant's
Pulumi state bucket were created by the platform's provisioning flow and live
in that flow's state -- a Pulumi program cannot create the identity it runs
as. The passphrase that decrypts this stack lives only in this repo's own
`PULUMI_CONFIG_PASSPHRASE` secret, minted once at provisioning time and never
held by the platform's provisioning flow afterward. What arrived here instead
at provisioning time: four stack config values (the database instance
connection name, the tenant image repository path, the media bucket URL, the
deployer service account email), four repo variables and one repo secret.

## Day-to-day

- CI (`.github/workflows/infra-ci.yml`) type-checks every pull request and
  applies `main` to the `__TENANT_NAME__` stack. Both jobs refuse to run if a
  template placeholder survives, and the deploy job's delete-guard preflight
  aborts any plan that would destroy this tenant's resources.
- Changes are pull requests against `Pulumi.__TENANT_NAME__.yaml` and
  `index.ts`. This stack's config secrets are encrypted with the passphrase
  in this repo's own `PULUMI_CONFIG_PASSPHRASE` -- set them with
  `pulumi config set --secret` from a checkout of this repo, with that
  environment variable exported locally, never by pasting plaintext into the
  file.
- Optional mail and bulk-email config keys are documented in the
  [template's README](https://github.com/branchLeft/ghost-platform-tenant-template#optional-mail-config).
- This stack's `encryptionsalt` belongs in this repo's
  `PULUMI_ENCRYPTION_SALT` secret, not in the committed config. **Check which
  state this repo is in before touching it:** if
  `Pulumi.__TENANT_NAME__.yaml` has no `encryptionsalt` line, the deploy job
  appends the secret to the working copy for that job alone, and to run
  `pulumi` locally you append your own held copy and do not commit it --
  `printf '\nencryptionsalt: %s\n' "$PULUMI_ENCRYPTION_SALT" >> Pulumi.__TENANT_NAME__.yaml`.
  If the file *does* carry that line, the provisioning flow committed it, the
  `Committed-secret guard` job is failing, and the deploy is running on the
  committed value. Fixing that is a documented procedure with an order that
  matters -- `RUNBOOK-bootstrap.md`, "The committed-secret guard fails on a
  freshly generated repo". Deleting the line first breaks the next deploy.
- `scripts/assert-no-committed-pulumi-secrets.py` fails a commit, and CI's
  `Committed-secret guard` job fails a pull request, that puts a salt back.

Bootstrap record and recovery paths: `RUNBOOK-bootstrap.md`.
