# index.ts

## `DIGEST_PINNED_IMAGE`

Registry host and path, an optional tag, and a mandatory `sha256` digest.
Deliberately the same shape `branchleft-deploy` enforces on the host: a tag
alone is a mutable pointer, so a stack deployed by tag has no answer to "what
is running" and a restart months later can silently change the image.

Checked here as well as there because the two refusals land in different
places. The host's refusal fails a deploy that has already been merged; this
one fails the pull request that would have merged it.

## The secrets env template (moved from `index.ts`, CMT-3)

The key names `/etc/branchleft/<slug>.env` needs, as the render core's
template: `KEY=` lines with empty values, never a value. Not a secret. The
host fills it, root-owned `0600`, with `app/provision/render_tenant_secrets.py`
from `branchLeft/ghost-platform`; this stack never holds the values.

## The fixed descriptor halves (moved from `index.ts`, CMT-3)

The fixed halves of a paying tenant's descriptor: every value here is the
only one a `tenant` kind may carry or the platform default, so none is a
config key. `as unknown as` because the render core brands its string and
number fields; `validateTenantStack()` inside the component is what proves
them. No `ownerEmail` and no credential: the stack holds no secret value, and
the host renders the secrets file.

## The media bucket (moved from `index.ts`, CMT-3)

This tenant's own Object Storage bucket, and the base URL Ghost writes into
every published post. Nothing here creates either: the bucket, its versioning
and the policy that fences it to this tenant's key are made by an operator
before this stack first applies, per `RUNBOOK-bootstrap.md`. Exported so that
what was created can be compared against what the container is configured
with — a mismatch is uploads failing after a deploy that reported success.
