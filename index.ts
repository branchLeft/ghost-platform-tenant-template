import * as pulumi from '@pulumi/pulumi';
import {
  DEFAULT_RESOURCE_CAPS,
  GhostTenant,
  databaseAndUserName,
  mediaBucketName,
  type TenantDescriptor,
} from '@branchleft/ghost-platform-tenant';
import {
  appHostPrivateIp,
  backupEncryptionRecipient,
  bulkEmailApiKey,
  bulkEmailDomain,
  databaseHost,
  databaseMaxUserConnections,
  databasePassword,
  databasePort,
  healthPort,
  hostPort,
  hostPortB,
  hostnameVerifiedAt,
  imageRef,
  mailCeiling,
  mailDkimSelector,
  mailEstateCeiling,
  mailHost,
  mailPassword,
  mailPort,
  mailUser,
  mediaAccessKeyId,
  mediaEndpoint,
  mediaRegion,
  mediaSecretAccessKey,
  ownerEmail,
  siteUrl,
  slug,
  uid,
  zones,
} from './config';

/** Registry host and path, an optional tag, and a mandatory `sha256` digest —
 * the same shape `branchleft-deploy` enforces on the host. See index.md. */
const DIGEST_PINNED_IMAGE =
  /^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:\/[a-z0-9]+(?:[._-][a-z0-9]+)*)*(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?@sha256:[0-9a-f]{64}$/;

if (!DIGEST_PINNED_IMAGE.test(imageRef)) {
  throw new Error(
    `imageRef must be digest-pinned, e.g. ghcr.io/branchleft/ghost@sha256:<64 hex>. ` +
      `Got: ${imageRef}`
  );
}

/** Where a descriptor's hostname sits: under the platform zone ("ours"), or a
 * verified custom domain ("theirs"). Derived from `siteUrl`, so the two cannot
 * disagree; `validate()` checks the result against `siteUrl` again. */
type Instant = Extract<TenantDescriptor['hostname'], { kind: 'theirs' }>['verifiedAt'];

function hostnameOf(url: string): TenantDescriptor['hostname'] {
  const host = new URL(url).hostname;
  const suffix = `.${zones.platformZone}`;
  if (host.endsWith(suffix)) {
    return { kind: 'ours', sub: host.slice(0, -suffix.length), gated: false };
  }
  if (hostnameVerifiedAt === undefined) {
    throw new Error(
      `siteUrl ${url} is outside ${zones.platformZone}; set hostnameVerifiedAt to when the ` +
        `custom domain was verified.`
    );
  }
  return { kind: 'theirs', fqdn: host, verifiedAt: hostnameVerifiedAt as Instant };
}

// The fixed halves of a paying tenant's descriptor: every value here is the
// only one a `tenant` kind may carry or the platform default, so none is a
// config key. `as unknown as` because the render core brands its string and
// number fields; `validate()` inside the component is what proves them.
const descriptor = {
  version: 1,
  kind: 'tenant',
  slug,
  siteUrl,
  image: imageRef,
  ownerEmail,
  uid,
  ports: { a: hostPort, b: hostPortB, health: healthPort },
  appHostIp: appHostPrivateIp,
  database: {
    kind: 'mysql',
    host: databaseHost,
    port: databasePort,
    name: databaseAndUserName(slug),
    user: databaseAndUserName(slug),
  },
  // The bucket is derived from the slug, so this stack holds no value that
  // could name another tenant's media. `mediaBucket` and `mediaPublicBaseUrl`
  // below are exported for the operator who has to create that bucket.
  media: {
    kind: 's3',
    endpoint: mediaEndpoint,
    region: mediaRegion,
    bucket: mediaBucketName(slug),
    resize: true,
    srcsets: true,
  },
  transport: { kind: 'smtp', host: mailHost, port: mailPort, user: mailUser },
  mail: {
    enabled: true,
    ceiling: mailCeiling,
    estateCeiling: mailEstateCeiling,
    identity: { kind: 'tenant', domain: bulkEmailDomain, dkimSelector: mailDkimSelector },
  },
  hostname: hostnameOf(siteUrl),
  gate: { kind: 'none' },
  backup: { kind: 'bucket-native', encryptionRecipient: backupEncryptionRecipient },
  codeInjection: { kind: 'blocked' },
  limits: { membersCap: null, staffCap: null },
  caps: DEFAULT_RESOURCE_CAPS,
  safety: { near: true, exact: true },
  breakGlass: { kind: 'disabled' },
  expiresAt: null,
} as unknown as TenantDescriptor;

const tenant = new GhostTenant(slug, {
  descriptor,
  zones,
  secrets: {
    databasePassword,
    s3AccessKeyId: mediaAccessKeyId,
    s3SecretAccessKey: mediaSecretAccessKey,
    mailPassword,
    bulkEmailApiKey,
  },
  ...(databaseMaxUserConnections === undefined
    ? {}
    : { maxUserConnections: databaseMaxUserConnections }),
});

/**
 * The exact content of `/etc/branchleft/<slug>.env`, root-owned `0600` on the
 * app host. A Pulumi secret: it carries this tenant's database password and,
 * where configured, its SMTP and bulk-mail credentials.
 *
 * Read with `pulumi stack output --show-secrets secretsEnvFile`. Written to the
 * host by an operator alone — no automated path may write this file, which is
 * why nothing in this repo's CI reads this output.
 */
export const secretsEnvFile = tenant.secretsEnvFile;

/** The exact content of `/opt/branchleft/<slug>/compose.yml`. Placed on the
 * host by an operator, for the same reason: every line of it is a
 * runtime-isolation control, and a stack that omits one still starts. */
export const composeFile = tenant.composeFile;

/** The root-run script that must create this tenant's volumes before its unit
 * is enabled. The rendered stack declares both volumes `external`, so skipping
 * it fails the unit start rather than coming up on a volume Docker seeded.
 * Replaces 4.x's `hostProvisioningCommand`. */
export const provisionScript = tenant.provisionScript;

/** The exact content of `/etc/branchleft/<slug>.image.env`. Today written by
 * `branchleft-deploy`, not by an operator from this output. */
export const imageEnvFile = tenant.imageEnvFile;

/** This tenant's site block for the edge's site registry, as JSON. */
export const edgeSiteBlock = tenant.edgeSiteBlock;

/** This tenant's Ghost settings document, as JSON. */
export const ghostSettings = tenant.ghostSettings;

/** This tenant's Caddy `request_body max_size`, for its site block in the
 * edge's site registry in `branchLeft/shared-infra`. Derived from the same
 * input as the tmpfs ceiling so the two cannot disagree; setting it by hand to
 * a different number defeats that. */
export const edgeRequestBodyMaxSize = tenant.edgeRequestBodyMaxSize;

export const composeUnit = tenant.composeUnit;
export const stackDirectory = tenant.stackDirectory;
export const secretsEnvPath = tenant.secretsEnvPath;
export const imageEnvPath = tenant.imageEnvPath;
export const databaseName = tenant.databaseName;
export const databaseUser = tenant.databaseUser;

/** This tenant's own Object Storage bucket, and the base URL Ghost writes into
 * every published post. Nothing here creates either: the bucket, its versioning
 * and the policy that fences it to this tenant's key are made by an operator
 * before this stack first applies, per `RUNBOOK-bootstrap.md`. Exported so that
 * what was created can be compared against what the container is configured
 * with — a mismatch is uploads failing after a deploy that reported success. */
export const mediaBucket = tenant.mediaBucket;
export const mediaPublicBaseUrl = tenant.mediaPublicBaseUrl;

/** Read by the deploy job, which pipes it to `branchleft-deploy` over this
 * repo's own slot key. Exported rather than read from config by the job so that
 * what is deployed is what this stack's last successful apply recorded. */
export const image = imageRef;

/** Read by `scripts/assert-no-tenant-deletes.py` out of this stack's own
 * preview plan: the fields whose change orphans live tenant data rather than
 * updating it. */
export const identity = tenant.identity;

/** Registered so `pulumi stack output` answers "which app host" without
 * reading the config file. */
export const appHost = pulumi.output(appHostPrivateIp);
