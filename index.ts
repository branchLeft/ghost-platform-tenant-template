import * as pulumi from '@pulumi/pulumi';
import { GhostTenant } from '@branchleft/ghost-platform-tenant';
import {
  appHostPrivateIp,
  bulkEmail,
  databaseHost,
  databaseMaxUserConnections,
  databasePassword,
  hostPort,
  imageRef,
  mail,
  mediaAccessKeyId,
  mediaEndpoint,
  mediaRegion,
  mediaSecretAccessKey,
  rssBudgetMib,
  siteUrl,
  slug,
  uid,
  uploadCeilingMib,
} from './config';

/**
 * Registry host and path, an optional tag, and a mandatory `sha256` digest.
 * Deliberately the same shape `branchleft-deploy` enforces on the host: a tag
 * alone is a mutable pointer, so a stack deployed by tag has no answer to "what
 * is running" and a restart months later can silently change the image.
 *
 * Checked here as well as there because the two refusals land in different
 * places. The host's refusal fails a deploy that has already been merged; this
 * one fails the pull request that would have merged it.
 */
const DIGEST_PINNED_IMAGE =
  /^[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?(?:\/[a-z0-9]+(?:[._-][a-z0-9]+)*)*(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?@sha256:[0-9a-f]{64}$/;

if (!DIGEST_PINNED_IMAGE.test(imageRef)) {
  throw new Error(
    `imageRef must be digest-pinned, e.g. ghcr.io/branchleft/ghost@sha256:<64 hex>. ` +
      `Got: ${imageRef}`
  );
}

const tenant = new GhostTenant(slug, {
  slug,
  siteUrl,
  uid,
  appHostPrivateIp,
  hostPort,
  database: {
    host: databaseHost,
    password: databasePassword,
    ...(databaseMaxUserConnections === undefined
      ? {}
      : { maxUserConnections: databaseMaxUserConnections }),
  },
  // The bucket and the public base URL are derived from the slug by the
  // component, so this stack holds no value that could name another tenant's
  // media. `mediaBucket` and `mediaPublicBaseUrl` below are exported for the
  // operator who has to create that bucket, not read back as inputs.
  media: {
    endpoint: mediaEndpoint,
    region: mediaRegion,
    accessKeyId: mediaAccessKeyId,
    secretAccessKey: mediaSecretAccessKey,
  },
  ...(mail === undefined ? {} : { mail }),
  ...(bulkEmail === undefined ? {} : { bulkEmail }),
  ...(uploadCeilingMib === undefined ? {} : { uploadCeilingMib }),
  ...(rssBudgetMib === undefined ? {} : { rssBudgetMib }),
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

/** The root-run command that must create this tenant's volumes before its unit
 * is enabled. The rendered stack declares both volumes `external`, so skipping
 * it fails the unit start rather than coming up on a volume Docker seeded. */
export const hostProvisioningCommand = tenant.hostProvisioningCommand;

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
