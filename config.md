# config.ts

Every value one tenant's stack needs, read from `Pulumi.<stack>.yaml`.

Every value is plain. They are written by the platform's provisioning workflow
when this repo is generated, are facts about where this tenant lives, and are
reviewable in the handover pull request's diff.

**There are no secret values.** Before 8.0.0 an
operator set six with `pulumi config set --secret` and the component wrote them
into a Pulumi secret output, so every credential sat in the stack's config and
its checkpoint. Now the host renders the secrets file, and this stack never
sees a value. `scripts/assert-no-secure-stack-config.py` refuses a `secure:`
entry, or a credential-named key, in a committed `Pulumi.<stack>.yaml`.

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
`zones.platformZone`. New keys: `hostPortB`, `healthPort`, `zones`,
`backupEncryptionRecipient`, `mailDkimSelector`, `mailCeiling`,
`mailEstateCeiling`, and optionally `databasePort` and `hostnameVerifiedAt`.
Mail is now required. Removed: `mailFrom`, `bulkEmailBaseUrl`,
`uploadCeilingMib`, `rssBudgetMib`.

## Since 8.0.0: secrets live on the host

The owner's email address, the database password, the Object Storage key pair,
the SMTP password and the mail spool's API key are no longer config keys.
`ownerEmail`, `databasePassword`, `mediaAccessKeyId`, `mediaSecretAccessKey`,
`mailPassword` and `bulkEmailApiKey` are read by nothing, and a stack that still
carries one is refused by `scripts/assert-no-secure-stack-config.py`. The
component takes no `secrets` argument; `index.ts` exports `secretsEnvTemplate`
(key names, empty values) and `secretKeys`, and the host fills
`/etc/branchleft/<slug>.env` with `app/provision/render_tenant_secrets.py` from
`branchLeft/ghost-platform`. The owner's address is a person's, and it reaches
Ghost only as `GHOST_OWNER_EMAIL` in that file. `zones.mailSpoolBaseUrl` is
gone since 7.0.0: Ghost reaches the host's mail spool by its service name.
`pnpm test:render` (`test/render.test.ts`) loads this program under Pulumi's
mocks with a sentinel under each former key and checks that it appears in no
export, registered output or resource input, and that no output is a secret.

## `imageRef`

The image this tenant runs, always digest-pinned.

Config rather than a repository variable so that changing which image a
tenant runs is a reviewed diff on a branch, the way it was when the GCP-era
stack passed a reference to Cloud Run. It now reaches the component as the descriptor's `image` and renders into
`imageEnvFile`; `branchleft-deploy` still writes
`/etc/branchleft/<slug>.image.env` on the host. This stack still exports
`image` so that an operator can read it. The deploy job does not read that
output: it reads `imageRef` from the config committed in the commit being
deployed, because a stack output lives in the state checkpoint and can be
rewritten by anything holding the state credential.

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

## The Object Storage key pair

Not a config key since 8.0.0: both halves, including the key id, go to the host
together, in the secrets file. The id is not itself a secret, but holding the
pair together is what makes rotating this credential one edit instead of two,
and a rotation that updates one half is a tenant whose media stops working with
a 403 that reads as a bucket-policy problem.

The pair is created in the Hetzner Cloud Console — there is no API that mints
one — and the bucket policy naming it as a principal is applied by the same
operator in the same sitting. A key with no policy fencing it is valid for
every bucket in its project.
