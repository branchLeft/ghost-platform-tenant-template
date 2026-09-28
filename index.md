# index.ts

## `DIGEST_PINNED_IMAGE`

Registry host and path, an optional tag, and a mandatory `sha256` digest.
Deliberately the same shape `branchleft-deploy` enforces on the host: a tag
alone is a mutable pointer, so a stack deployed by tag has no answer to "what
is running" and a restart months later can silently change the image.

Checked here as well as there because the two refusals land in different
places. The host's refusal fails a deploy that has already been merged; this
one fails the pull request that would have merged it.
