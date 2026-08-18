# CLAUDE.md — Ghost platform tenant stack

One Pulumi program (`index.ts`), one stack, one tenant: a tenant's own name,
hostname and config invoking the tenant-anonymous `GhostTenant` component from
`@branchleft/ghost-platform-tenant`. The template's `README.md` has the repo topology
and the placeholder table (`__TENANT_NAME__` and friends); in a generated repo,
`README.md` is the substituted `README.tenant.md`, describing that tenant's own
stack.

**This repo declares no identity.** The deployer service account, its Workload
Identity pool and provider, its project roles and the Pulumi state bucket are
created by the platform's provisioning flow and live in that stack's state —
not here. The passphrase that decrypts this stack lives only in this repo's
own `PULUMI_CONFIG_PASSPHRASE` secret, minted once at provisioning time. The
stack's `encryptionsalt` is not committed either (`branchLeft/standards`
PUL-12): it is held in `PULUMI_ENCRYPTION_SALT` and appended to the working
copy of the stack config by the deploy job. Architecture and rationale live in
the platform's private architecture documentation.

**Private, and a tenant roster of one.** A hostname plus Cloud Run service name
is that tenant's identity. Never carry it into a public repo, an issue, or a PR
description outside this repo.

## graphify

`graphify-out/` holds a knowledge graph of this repo, rebuilt by CI on every push to `main` and published as a `chore(graphify)` PR.

- Answer codebase and architecture questions with `graphify query "<question>"` first — `graphify path "<A>" "<B>"` for a relationship, `graphify explain "<concept>"` for a concept. Each returns a scoped subgraph, far smaller than the equivalent grep. The payload files behind it are read-blocked in `.claude/settings.json` — go through the query commands instead.
- If a `chore(graphify)` PR is open, the graph you have is behind — get it merged and pulled before reasoning from it.
- After changing code, `graphify update .` refreshes the graph locally. AST-only, no API cost.
- `graphify-out/.graphify_root` and `.graphify_python` are never committed: they record absolute paths on the machine that built the graph, and a foreign value in either one is worse than its absence.
