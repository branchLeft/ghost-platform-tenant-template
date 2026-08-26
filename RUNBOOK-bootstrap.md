# Runbook — a generated tenant repo

Onboarding is not finished when this repo appears. The provisioning flow creates
the repo, mints and escrows this stack's passphrase, writes the non-secret
config and opens a handover pull request; **everything that needs a credential
or touches a host is done by an operator afterwards**, and this file is the
tenant-side half of that. The whole sequence, including the host-side steps,
lives in `branchLeft/ghost-platform`'s `RUNBOOK-tenant-onboarding.md`.

That split is the design, not an unfinished automation. Provisioning runs on a
GitHub-hosted runner: it cannot reach `db1` or the app host, which are on a
private network, and giving it a credential that could install credentials on
every app host is exactly the thing the deploy design exists not to have.

---

## What an operator finishes in the handover pull request

The pull request arrives carrying `Pulumi.<slug>.yaml` with the plain config
values only. Three things have to be added to it before it can merge, from a
local checkout of the `provisioning/handover` branch.

**Set the environment up first, in this order.** The salt step is the one that
matters: `Pulumi.<slug>.yaml` is handed over without an `encryptionsalt`, and a
`pulumi config set --secret` against a file that has none mints a _new_ salt
into it — after which the stack's checkpoint and its own config disagree about
which key its secrets are under, and nothing says so until a deploy fails to
decrypt them.

```bash
export PULUMI_CONFIG_PASSPHRASE='<the escrowed value, decrypted>'
export AWS_ACCESS_KEY_ID='<Hetzner S3 access key id>'
export AWS_SECRET_ACCESS_KEY='<Hetzner S3 secret access key>'
pulumi login "$(gh variable get PULUMI_BACKEND_URL --repo branchLeft/<generated-repo>)"

printf '\nencryptionsalt: %s\n' '<this stack's salt>' >> Pulumi.<slug>.yaml
```

The salt is in this repo's `PULUMI_ENCRYPTION_SALT` environment secret, which is
write-only. If you no longer hold a copy, read it back out of the checkpoint:

```bash
pulumi stack export --stack <slug> \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["deployment"]["secrets_providers"]["state"]["salt"])'
```

Then:

1. **The database password**, printed once by `provision_tenant_db.py` on `db1`
   and printed by nothing afterwards:

   ```bash
   pulumi config set --secret databasePassword --stack <slug>
   ```

2. **This tenant's Object Storage key pair**, both halves secret:

   ```bash
   pulumi config set --secret mediaAccessKeyId --stack <slug>
   pulumi config set --secret mediaSecretAccessKey --stack <slug>
   ```

   This tenant's media bucket, `branchleft-media-<slug>`, and the bucket policy
   that fences this key to it must already exist —
   `branchLeft/ghost-platform`'s `RUNBOOK-tenant-onboarding.md` §6 creates and
   verifies both. Nothing in this stack creates them, and nothing fails if they
   are missing until the first upload: the deploy succeeds and the tenant's
   media 404s. Confirm the bucket name after the first apply with
   `pulumi stack output mediaBucket --stack <slug>`.

3. **`known_hosts`**, filled in with the app host's SSH host key. Take it from
   the host itself over your own root session — never `ssh-keyscan`, which is
   trust-on-first-use and records whatever answered:

   ```bash
   ssh -i ~/.ssh/id_ed25519_hetzner root@<app-host-public-ipv4> \
     'cat /etc/ssh/ssh_host_ed25519_key.pub'
   ```

   Write one line: `<app-host-public-ipv4> ` followed by that whole output.

**Then take the salt back out before committing.** It must not reach the commit
(`branchLeft/standards` PUL-12), and the `Committed-secret guard` job fails
while it is there:

```bash
python3 - <<'EOF'
import pathlib, re
p = next(pathlib.Path('.').glob('Pulumi.*.yaml'))
p.write_text(re.sub(r'(?m)^encryptionsalt:.*\n', '', p.read_text()))
EOF
python3 scripts/assert-no-committed-pulumi-secrets.py --scan-tree .
```

Each of the three values above is one a `workflow_dispatch` input could not have carried:
inputs are plaintext in the run's API response and in its form. That the
passphrase has to be used here, on day one, is deliberate — it is what proves
the escrow works while the tenant is still new, rather than the first time
anyone needs it being an incident.

**Do not merge until the four host-side steps have run** and this repo's
`APP_HOST_DEPLOY_KEY` environment secret is set. Merging runs the deploy job,
and the deploy fails — loudly, which is the intent — if the slot has no key, if
the Compose file is not on the host, or if the volumes were never provisioned.

---

## The confirmation test

Merging is itself the test. A healthy first run:

- applies the stack, reporting the component and its outputs;
- reads `image` from the applied stack;
- pipes it over the slot key, and `branchleft-deploy` pins the digest and
  restarts `branchleft-compose@<slug>`.

```bash
gh run list --repo branchLeft/<generated-repo> --workflow "Infra CI" --limit 3
gh run view --repo branchLeft/<generated-repo> <run-id> --log-failed
```

**A green run is not by itself proof the deploy did anything.** A skipped job
still reports the overall run as successful. Check that `Deploy` actually ran
and read its job summary, which names the digest that reached the host.

**Every later merge to `main` is a restart of this tenant's site, not only the
first.** The deploy step runs on every push to `main` and `branchleft-deploy`
has no same-digest no-op: it rewrites `/etc/branchleft/<slug>.image.env` and
restarts `branchleft-compose@<slug>` whatever the reference. So a merge that
only changes a comment in `index.ts` still takes Ghost down for the length of a
`docker compose up --wait`. Batch cosmetic changes, and do not merge one during
an incident on this tenant.

---

## What CI deliberately cannot do

General rule: `standards/docs/infrastructure.md` IAC-1 — the signal is a refusal,
the fix a privileged operation, never widening the credential to silence it.

- **Write `/etc/branchleft/<slug>.env` or `/opt/branchleft/<slug>/compose.yml`.**
  `branchleft-deploy` writes `<slug>.image.env` and nothing else, and it is the
  only command the slot key can run. Both other files are root-owned, and
  between them they are the runtime-isolation posture.
- **Deploy any other tenant.** The slot key's `authorized_keys` entry carries a
  forced command naming this stack, so sshd runs exactly that whatever the
  client asks for. There is no argument position for a second name.
- **Get a shell on the app host.** `restrict` removes pty, agent forwarding and
  port forwarding, and the forced command replaces subsystem requests too.
- **Create this tenant's database, DB user, volumes or UID claim.** All four are
  root-run scripts on hosts a GitHub runner cannot reach.
- **Change its own credentials.** They are environment secrets on `production`;
  a workflow run cannot write a secret.
- **Reach the estate's own Pulumi state.** The state backend this repo logs into
  is a bucket holding tenant stacks alone. It deliberately is not the bucket the
  estate's checkpoint — and the production hcloud token inside it — lives in.

The one reach worth stating plainly rather than burying: the Object Storage
credential that reaches this stack's state reaches **every tenant stack in that
bucket**, because an S3 credential is not scoped per stack. That is a known,
recorded property of the state backend, not something this repo can narrow.

---

## Rotating this stack's passphrase

Rotating means **re-wrapping the stack**, not replacing the secret. Replacing
only the secret leaves a checkpoint the new value cannot decrypt, and a stack
whose passphrase is gone cannot even be `pulumi destroy`ed — destroy reads a
checkpoint it can no longer decrypt.

The order, from a checkout with the _old_ value exported:

```bash
pulumi stack change-secrets-provider passphrase --stack <slug>   # prompts for the new value
gh secret set PULUMI_CONFIG_PASSPHRASE --repo branchLeft/<generated-repo> --env production
gh secret set PULUMI_ENCRYPTION_SALT  --repo branchLeft/<generated-repo> --env production
```

The salt changes with the passphrase, so both secrets move together. Escrow the
new value before deleting the old one from wherever it is held.

---

## The committed-secret guard fails on a freshly generated repo

`Committed-secret guard` failing on a repo nobody has edited means
`Pulumi.<slug>.yaml` arrived with an `encryptionsalt` line in it. The deploy
still works — the salt step reads the committed value, warns, and restores
nothing — so this is a fix to make, not an outage.

**Set the secret first.** Deleting the line before the secret exists leaves the
next deploy with nothing to decrypt the stack. The failing annotation carries the
value, the file and the line number, so take the `v1:`-prefixed value from it,
without the `encryptionsalt: ` key in front:

```bash
gh secret set PULUMI_ENCRYPTION_SALT --repo branchLeft/<generated-repo> --env production
```

It reads from stdin when no `--body` is given, which keeps it out of shell
history.

**Then delete the line the annotation names**, in an editor. No `sed` or `grep`
recipe is given here on purpose: the guard matches quoted (`"encryptionsalt":`)
and byte-order-mark-prefixed forms that a one-line pattern written from memory
does not, so a recipe that looks like it worked can leave the salt in place and
the guard red with nothing left to try. The annotation's line number is exact
whatever form the key took.

**Then confirm, before committing:**

```bash
python3 scripts/assert-no-committed-pulumi-secrets.py Pulumi.<slug>.yaml
```

It prints nothing and exits 0 when the file is clean. That is the same check CI
runs, so a pass here is a pass there.

---

## Tearing this tenant down

Order matters, and two of these steps are unrecoverable in the wrong one. Full
version, with the platform-side steps, in `branchLeft/ghost-platform`'s
`RUNBOOK-tenant-onboarding.md`.

1. **Revoke the slot first**, so nothing can redeploy while the rest is
   dismantled:
   `provision_deploy_slot.py --revoke <slug>` on the app host, as root.
2. Stop and disable the unit: `systemctl disable --now branchleft-compose@<slug>`.
3. Take the final backups you intend to keep — the database dump and the whole
   of this tenant's media bucket, `branchleft-media-<slug>`. After step 5 there
   is no configured place to put them back.
4. `pulumi destroy` **before** anything deletes this repo or its passphrase
   secret. A destroy reads the checkpoint, so a repo deleted first strands the
   stack permanently.
5. Remove the host-side state: the secrets file, the Compose directory, the two
   volumes, the UID claim, then the tenant's database and DB user on `db1`.
6. Remove this tenant's site block from the edge's site registry.
7. Archive the repo rather than deleting it, unless the tenant asked otherwise.
8. Delete this tenant's Object Storage **credential first, then its bucket**, in
   the Hetzner Cloud Console. Both count against account-wide allowances (200
   credentials and 100 buckets across all projects), so leaving them spends a
   fixed budget on a tenant that no longer exists. Credential before bucket: a
   bucket deleted while its key survives leaves a key with no policy fencing it,
   valid for every other bucket in the project. Emptying the bucket needs the
   operator's key — the tenant's cannot delete.

A tenant removed without step 1 leaves a working deploy key for a stack that no
longer exists, and on a host that has not been rebuilt the on-host register is
the only place that would show it.

---

## Onboarding another tenant

Not this runbook and not this repo. Onboarding is a run of the platform's
provisioning flow plus its host-side steps —
`branchLeft/ghost-platform`'s `RUNBOOK-tenant-onboarding.md`. There is no
multi-stack path inside a single generated repo.
