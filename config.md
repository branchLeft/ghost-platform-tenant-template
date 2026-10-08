# config.ts

Every value one tenant's stack needs, read from `Pulumi.<stack>.yaml`.

Split by who writes it, because the two halves arrive at different times and
by different routes:

- **Plain values** are written by the platform's provisioning workflow when
  this repo is generated. They are facts about where this tenant lives, and
  they are reviewable in the handover pull request's diff.
- **Secret values** are set by an operator with `pulumi config set --secret`
  in that same pull request, never by the provisioning workflow. Each one is
  either printed once on a host (`provision_tenant_db.py`) or created in a
  provider console (the Object Storage key pair), and a `workflow_dispatch`
  input is plaintext in the run's API response and its form — so there is no
  route by which provisioning could carry one without publishing it.

`require`, never `get`, for anything the container cannot boot without: an
unset value has to fail at `pulumi preview` rather than render a stack whose
secrets file is missing a line.

## `uid`

This tenant's reserved UID on its app host.

Allocated against the host by `app/provision/provision_tenant_volume.py
--list-claims`, never derived from the slug: it is host state, and a number
computed here would collide the first time two hosts disagreed about who
lives where. Recorded in config so the value this stack renders is the value
the host was provisioned with, and a drift between them is a diff.

## Since 6.0.0: a descriptor

`index.ts` builds the render core's `TenantDescriptor` from these keys and hands
it to the component, which validates and renders it. Fields a paying tenant has
only one valid value for (kind, gate, code injection, break-glass, safety,
limits, caps, media resize and srcsets, expiry) are constants in `index.ts`, not
keys. The database name and user, and the media bucket, are derived from the
slug, never settable here. `hostname` is derived from `siteUrl` and
`zones.platformZone`. New keys: `hostPortB`, `healthPort`, `ownerEmail`,
`zones`, `backupEncryptionRecipient`, `mailDkimSelector`, `mailCeiling`,
`mailEstateCeiling`, and optionally `databasePort` and `hostnameVerifiedAt`.
Mail is now required. Removed: `mailFrom`, `bulkEmailBaseUrl`,
`uploadCeilingMib`, `rssBudgetMib`.

## `imageRef`

The image this tenant runs, always digest-pinned.

Config rather than a repository variable so that changing which image a
tenant runs is a reviewed diff on a branch, the way it was when the GCP-era
stack passed a reference to Cloud Run. It now reaches the component as the descriptor's `image` and renders into
`imageEnvFile`; `branchleft-deploy` still writes
`/etc/branchleft/<slug>.image.env` on the host, and this stack still exports
`image` for the deploy job to read.

## `mediaEndpoint` / `mediaRegion`

Object Storage addressing, and only the platform-wide half of it.

This tenant's media bucket is `branchleft-media-<slug>` and its public base
URL is that bucket under this endpoint — both derived from the slug inside
the component, neither settable here. That is the isolation control: the
bucket is the only boundary between this tenant's media and another's, so a
config key naming it would be a config key that could name someone else's.

The endpoint host and the region must name the same Object Storage location.
Against Ceph RGW the region is part of the SigV4 credential scope, so a
mismatch is a signature failure surfacing as an opaque 403 that reads as a
credential problem.

## `mediaAccessKeyId` / `mediaSecretAccessKey`

Both halves of the Object Storage key pair are secret config, including the
key id.

The id is not itself a secret. Holding the pair together is what makes
rotating this credential one edit instead of two, and a rotation that updates
one half is a tenant whose media stops working with a 403 that reads as a
bucket-policy problem.

The pair is created in the Hetzner Cloud Console — there is no API that mints
one — and the bucket policy naming it as a principal is applied by the same
operator in the same sitting. A key with no policy fencing it is valid for
every bucket in its project.
