import * as pulumi from '@pulumi/pulumi';

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

/** This tenant's host-side port, distinct per tenant on that host. */
export const hostPort = config.requireNumber('hostPort');

/** The image this tenant runs, always digest-pinned — config rather than a
 * repository variable, so which image runs is a reviewed diff. See config.md. */
export const imageRef = config.require('imageRef');

/** `db1`'s private address. */
export const databaseHost = config.require('databaseHost');

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

/** The single number every upload-related limit derives from, in MiB. Left
 * unset, the component's own default applies. */
export const uploadCeilingMib = config.getNumber('uploadCeilingMib');
export const rssBudgetMib = config.getNumber('rssBudgetMib');

/** Optional mail, all-or-nothing: once `mailHost` is set the rest is
 * `require`d, so a half-configured block fails at preview, not silently. */
const mailHost = config.get('mailHost');
export const mail = mailHost
  ? {
      host: mailHost,
      port: config.getNumber('mailPort') ?? 587,
      user: config.require('mailUser'),
      from: config.require('mailFrom'),
      password: config.requireSecret('mailPassword'),
    }
  : undefined;

/** Optional bulk email, same all-or-nothing shape as mail above. */
const bulkEmailBaseUrl = config.get('bulkEmailBaseUrl');
export const bulkEmail = bulkEmailBaseUrl
  ? {
      baseUrl: bulkEmailBaseUrl,
      domain: config.require('bulkEmailDomain'),
      apiKey: config.requireSecret('bulkEmailApiKey'),
    }
  : undefined;
