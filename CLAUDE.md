# CLAUDE.md — Ghost platform tenant stack

One Pulumi program (`index.ts`), one stack, one tenant: a tenant's own slug,
hostname, UID, port and credentials invoking the tenant-anonymous `GhostTenant`
component from `@branchleft/ghost-platform-tenant`. The template's `README.md`
has the config tables and the placeholder table; in a generated repo,
`README.md` is the substituted `README.tenant.md`.

**This stack creates nothing.** The component declares no cloud resources —
every durable thing a tenant uses is shared and already exists. What it renders
is configuration: a Compose file and a secrets file, both of which an operator
places on the app host by hand. No automated path may write either; between them
they are the runtime-isolation posture, and a stack that quietly loses one line
of it still starts and still serves.

**The only thing CI puts on a host is an image digest**, piped to
`branchleft-deploy` over this repo's own slot key. That key's `authorized_keys`
entry carries a forced command naming this stack, so it cannot address another
tenant — there is no caller-supplied stack name on the path.

**No GCP.** No service account, no Workload Identity Federation, no KMS, no GCS
bucket. State is Hetzner Object Storage reached through `PULUMI_BACKEND_URL`;
this stack's secrets are wrapped by a passphrase minted for this tenant alone,
held in `PULUMI_CONFIG_PASSPHRASE`. The `encryptionsalt` is not committed
(`branchLeft/standards` PUL-12): it lives in `PULUMI_ENCRYPTION_SALT` and the
deploy job appends it to the working copy.

**Every credential is an environment secret on `production`**, never a
repository secret — a repository secret is readable by any workflow run,
including one from a branch. That scoping is the replacement for the
provider-enforced repository pin Workload Identity Federation used to give.

**A hostname plus a slug is that tenant's identity.** Never carry it into a
public repo, an issue, or a PR description outside this repo.

<!-- template-only:start -->
## graphify

`graphify-out/` holds a knowledge graph of this repo, rebuilt by CI on every push to `main` and published as a `chore(graphify)` PR.

- Answer codebase and architecture questions with `graphify query "<question>"` first — `graphify path "<A>" "<B>"` for a relationship, `graphify explain "<concept>"` for a concept. Each returns a scoped subgraph, far smaller than the equivalent grep. The payload files behind it are read-blocked in `.claude/settings.json` — go through the query commands instead.
- If a `chore(graphify)` PR is open, the graph you have is behind — get it merged and pulled before reasoning from it.
- After changing code, `graphify update .` refreshes the graph locally. AST-only, no API cost.
- `graphify-out/.graphify_root` and `.graphify_python` are never committed: they record absolute paths on the machine that built the graph, and a foreign value in either one is worse than its absence.
<!-- template-only:end -->
