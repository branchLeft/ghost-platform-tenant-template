# Runbook — a generated tenant repo

**There is nothing to configure in this repo, per tenant, by hand.** No local
`pulumi up`, no `gcloud` grant, no repo variable, no secret. Everything a
tenant needs is created by the platform's provisioning flow, under the
platform's provisioning identity, before this repo is handed over —
`branchLeft/ghost-platform`'s `infra/platform/RUNBOOK-bootstrap.md` is where
that identity is set up, once, for the whole platform.

This file exists for what happens _after_ handover: the one approval that
completes onboarding, what CI deliberately cannot do, and how to recover when
it refuses.

---

## The one action a human takes here

**Merge the pull request the provisioning flow opened.** It commits
`Pulumi.<tenant-name>.yaml` — this stack's plain config values. Neither of the
two values that decrypt it is in there: the passphrase lives only in this
repo's own `PULUMI_CONFIG_PASSPHRASE` secret, and the encryption salt only in
`PULUMI_ENCRYPTION_SALT`, which the deploy job appends to the working copy of
that file and never commits back. Until the PR merges, CI has no stack config
to read and the deploy job fails.

Merging is itself the confirmation test: the push to `main` runs the `deploy`
job, and a healthy first run finds nothing to do. `pulumi up` reporting
`Resources: N unchanged` is the success condition, because the provisioning
flow already applied everything. That single run proves the federation, the
state-bucket access, this repo's own passphrase secret, the package-read
token and every project role work, without changing anything.

**A green run is not by itself proof the deploy did anything.** A skipped job
still reports the overall run as successful. Check that `Deploy (pulumi up)`
actually ran, and read its job summary.

```bash
gh run list --repo branchLeft/<generated-repo> --workflow "Infra CI" --limit 3
gh run view --repo branchLeft/<generated-repo> <run-id> --log-failed
```

Nothing in that PR is a value that can be entered wrongly. If
`imageDigestOrTag` looks wrong, that is worth a glance before merging; there
is nothing else in the file a reviewer can usefully second-guess.

---

## What this repo holds, and what it does not

This program declares one tenant's workload and one IAM binding. It does not
declare the identity it runs as — a Pulumi program cannot create the identity
it runs as, and the roles needed to try (`iam.serviceAccountAdmin`,
`resourcemanager.projectIamAdmin`) are exactly the ones a deploy identity must
never hold. The deployer service account, its Workload Identity pool and
provider, its project roles and this tenant's state bucket all live in the
platform's provisioning state. The passphrase that decrypts this stack lives
only in this repo's own `PULUMI_CONFIG_PASSPHRASE` secret, minted once at
onboarding time and never held by the platform's provisioning flow afterward.

Four stack config values come from there too, written into
`Pulumi.<tenant-name>.yaml` at provisioning time rather than read from a
`pulumi.StackReference`: the database instance connection name, the tenant
image repository path, the media bucket URL, and the deployer service account
email. A stack reference cannot cross backends, and each tenant now has its
own state bucket.

Mail and bulk email are both separate from all of that: each is optional,
per-tenant, and not part of provisioning. See the README's "Optional mail
config" and "Optional bulk-email config" tables for the keys, and
`GhostTenantMailArgs`/`GhostTenantBulkEmailArgs` in
`@branchleft/ghost-platform-tenant` for what they become.

**Once that mail config is live, also set Ghost's members support address to
the tenant's authorized sending address** (Admin → Settings → Membership,
"Support email address") — Ghost emails a confirmation link to that address
to verify the change, so the mailbox must exist and be readable first.
Skipped, this leaves member magic links failing with an opaque HTTP 400: the
delivery host rejects the default `noreply@` sender with `501 5.5.4`, and
nothing in Ghost surfaces that as the cause. See the platform's mail
architecture documentation for the full mechanics.

The one binding this repo does declare —
`deployer-can-act-as-<tenant-name>-sa` — is here because it cannot be
anywhere else: it names the runtime service account that this stack's own
first apply creates.

---

## Several things CI deliberately cannot do

General rule: `standards/docs/infrastructure.md` IAC-1 — the signal is a CI
403, the fix a privileged apply, never widening the deployer to silence it.

Each fails loudly with a 403 rather than silently, and because a failed
resource aborts the whole `pulumi up`, everything else in that run is blocked
too until the change is applied by an identity that holds the permission.
This deliberately includes creating or rotating the tenant's DB user
password, rotating the HMAC media-upload key, changing the tenant's
storage-prefix IAM conditions, and writing or creating a Secret Manager
version for any of the tenant's secrets (adding mail config to an
already-provisioned tenant, say) — the deployer's role is scoped tightly
enough that all of these need a platform-owner grant before CI's next run
reports `unchanged` and resumes. It also cannot change its own identity,
federation or roles, none of which it declares. The exact permission
boundaries and the reasoning behind each one are recorded in the platform's
private architecture documentation, not here.

Recovery is the same pattern the rest of the programme uses: grant or apply by
hand under a credential that holds the permission, `pulumi import` it into
state, then merge.

**A change that would replace or delete the tenant's service account,
database, DB user, Cloud Run service or media HMAC key** is blocked before
`pulumi up` runs, by `scripts/assert-no-tenant-deletes.py`. If one is
genuinely wanted it is a deliberate apply with the plan read line by line, not
a merge.

**Only `main` can authenticate.** The provider's `attributeCondition` requires
`assertion.ref == "refs/heads/main"`, so a workflow run from any other branch
cannot exchange a token at all.

---

## The committed-secret guard fails on a freshly generated repo

`Committed-secret guard` failing on a repo nobody has edited means
`Pulumi.<tenant-name>.yaml` arrived with an `encryptionsalt` line in it. The
deploy still works — the salt step reads the committed value, warns, and
restores nothing — so this is a fix to make, not an outage.

Take the value from the failing annotation, set it as the secret, and delete the
line:

```bash
gh secret set PULUMI_ENCRYPTION_SALT --repo branchLeft/<generated-repo>
grep -v '^[[:space:]]*encryptionsalt[[:space:]]*:' Pulumi.<tenant-name>.yaml > salt-free.tmp
mv salt-free.tmp Pulumi.<tenant-name>.yaml
```

Commit the deletion on a branch and merge it. `gh secret set` reads the value
from stdin when no `--body` is given, which keeps it out of shell history, and
`grep -v` is written in place of `sed -i` because the two spell in-place
editing differently on macOS and on Linux. Deleting the line without setting the
secret first leaves the next deploy with nothing to decrypt the stack.

---

## Onboarding another tenant

Not this runbook, and not this repo. Onboarding is a run of the platform's
provisioning flow, which generates a new repo from
`ghost-platform-tenant-template` and provisions its identity, state bucket and
first apply. There is no multi-stack path inside a single generated repo.
