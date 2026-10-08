import * as pulumi from '@pulumi/pulumi';
import type { ZoneConfig } from '@branchleft/ghost-platform-tenant';

const config = new pulumi.Config();

/** Every value one tenant's stack needs, read from `Pulumi.<stack>.yaml`. See
 * config.md for the plain/secret split and why fields below use `require`. */

/**
 * This tenant's slug: the Compose project, the systemd instance, the directory
 * under `/opt/branchleft`, the MySQL database and account name, and both volume
 * names. Equal to the Pulumi stack name.
 */
export const slug = config.require('slug');

/** Public site URL including protocol. Ghost refuses to boot without one. */
export const siteUrl = config.require('siteUrl');

/** This tenant's reserved UID on its app host, allocated against the host, not
 * derived from the slug. See config.md. */
export const uid = config.requireNumber('uid');

/** The app host's private address. Every published port binds this alone. */
export const appHostPrivateIp = config.require('appHostPrivateIp');

/** This tenant's host-side port for blue/green slot A (`ports.a`), distinct per
 * tenant on that host. Slot B and the health port follow. */
export const hostPort = config.requireNumber('hostPort');

/** Blue/green slot B's host-side port (`ports.b`). New with 6.0.0: the component
 * renders two Ghost services, so a tenant holds a pair of ports and a health
 * port, all three distinct. */
export const hostPortB = config.requireNumber('hostPortB');

/** The sidecar port the edge probes (`ports.health`); not Ghost's own port. */
export const healthPort = config.requireNumber('healthPort');

/** The address Ghost creates the owner account with (`ownerEmail`). */
export const ownerEmail = config.require('ownerEmail');

/** The image this tenant runs, always digest-pinned — config rather than a
 * repository variable, so which image runs is a reviewed diff. See config.md. */
export const imageRef = config.require('imageRef');

/** `db1`'s private address. */
export const databaseHost = config.require('databaseHost');

/** `db1`'s MySQL port. */
export const databasePort = config.getNumber('databasePort') ?? 3306;

/** Printed once by `db/provision/provision_tenant_db.py`; a re-run leaves an
 * existing password alone. Lose this value and the recovery is a password
 * reset on `db1`, not a lookup. */
export const databasePassword = config.requireSecret('databasePassword');

/** Applied on `db1` by the provisioning script; recorded here so the cap this
 * tenant is subject to is visible in its own repo. */
export const databaseMaxUserConnections = config.getNumber('databaseMaxUserConnections');

/** Object Storage addressing, platform-wide half only — the bucket and its
 * public base URL are derived from the slug inside the component, neither
 * settable here. Endpoint and region must name the same location. See
 * config.md. */
export const mediaEndpoint = config.require('mediaEndpoint');
export const mediaRegion = config.require('mediaRegion');

/** Both halves of the Object Storage key pair, including the id, which is not
 * itself secret — held together so rotation is one edit, not two. See
 * config.md. */
export const mediaAccessKeyId = config.requireSecret('mediaAccessKeyId');
export const mediaSecretAccessKey = config.requireSecret('mediaSecretAccessKey');

/**
 * The zones every hostname and sending domain is checked against: `demoZone`,
 * `platformZone`, `ownedDomains`, `demoMailDomain` and `mailSpoolBaseUrl`,
 * the render core's `ZoneConfig`. Platform-wide facts, identical across
 * tenants; `imagesWithBreakGlassAdapter` is optional.
 */
export const zones = config.requireObject<ZoneConfig>('zones');

/** When this tenant's custom domain was verified (an ISO instant). Required
 * only when `siteUrl` is outside `zones.platformZone`. */
export const hostnameVerifiedAt = config.get('hostnameVerifiedAt');

/** The tenant's own backup encryption recipient (a single age public
 * recipient). Exactly one per tenant, which is what makes erasure a key
 * destruction. */
export const backupEncryptionRecipient = config.require('backupEncryptionRecipient');

/** SMTP submission. Required since 6.0.0: the descriptor always carries a mail
 * transport. */
export const mailHost = config.require('mailHost');
export const mailPort = config.getNumber('mailPort') ?? 587;
export const mailUser = config.require('mailUser');
export const mailPassword = config.requireSecret('mailPassword');

/** The tenant's own sending identity: the domain it signs and the DKIM
 * selector. The domain must sit outside every domain the platform owns. */
export const bulkEmailDomain = config.require('bulkEmailDomain');
export const mailDkimSelector = config.require('mailDkimSelector');

/** The mail spool's Mailgun-shaped API key. */
export const bulkEmailApiKey = config.requireSecret('bulkEmailApiKey');

/** Per-tenant and estate-wide send ceilings. */
export const mailCeiling = config.requireNumber('mailCeiling');
export const mailEstateCeiling = config.requireNumber('mailEstateCeiling');
