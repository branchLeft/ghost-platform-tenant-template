import * as pulumi from '@pulumi/pulumi';

const config = new pulumi.Config();

/**
 * Every value one tenant's stack needs, read from `Pulumi.<stack>.yaml`.
 *
 * Split by who writes it, because the two halves arrive at different times and
 * by different routes:
 *
 * - **Plain values** are written by the platform's provisioning workflow when
 *   this repo is generated. They are facts about where this tenant lives, and
 *   they are reviewable in the handover pull request's diff.
 * - **Secret values** are set by an operator with `pulumi config set --secret`
 *   in that same pull request, never by the provisioning workflow. Each one is
 *   either printed once on a host (`provision_tenant_db.py`) or created in a
 *   provider console (the Object Storage key pair), and a `workflow_dispatch`
 *   input is plaintext in the run's API response and its form — so there is no
 *   route by which provisioning could carry one without publishing it.
 *
 * `require`, never `get`, for anything the container cannot boot without: an
 * unset value has to fail at `pulumi preview` rather than render a stack whose
 * secrets file is missing a line.
 */

/**
 * This tenant's slug: the Compose project, the systemd instance, the directory
 * under `/opt/branchleft`, the MySQL database and account name, and both volume
 * names. Equal to the Pulumi stack name.
 */
export const slug = config.require('slug');

/** Public site URL including protocol. Ghost refuses to boot without one. */
export const siteUrl = config.require('siteUrl');

/**
 * This tenant's reserved UID on its app host.
 *
 * Allocated against the host by `app/provision/provision_tenant_volume.py
 * --list-claims`, never derived from the slug: it is host state, and a number
 * computed here would collide the first time two hosts disagreed about who
 * lives where. Recorded in config so the value this stack renders is the value
 * the host was provisioned with, and a drift between them is a diff.
 */
export const uid = config.requireNumber('uid');

/** The app host's private address. Every published port binds this alone. */
export const appHostPrivateIp = config.require('appHostPrivateIp');

/** This tenant's host-side port, distinct per tenant on that host. */
export const hostPort = config.requireNumber('hostPort');

/**
 * The image this tenant runs, always digest-pinned.
 *
 * Config rather than a repository variable so that changing which image a
 * tenant runs is a reviewed diff on a branch, the way it was when the GCP-era
 * stack passed a reference to Cloud Run. Nothing in the rendered Compose file
 * carries it — `branchleft-deploy` writes it to
 * `/etc/branchleft/<slug>.image.env` on the host — so this stack exports it for
 * the deploy job to read rather than handing it to the component.
 */
export const imageRef = config.require('imageRef');

/** `db1`'s private address. */
export const databaseHost = config.require('databaseHost');

/**
 * Printed once by `db/provision/provision_tenant_db.py` when it created this
 * tenant's account, and printed by nothing afterwards — a re-run of that script
 * leaves an existing password alone and says nothing about it. Lose this value
 * and the recovery is a password reset on `db1`, not a lookup.
 */
export const databasePassword = config.requireSecret('databasePassword');

/** Applied on `db1` by the provisioning script; recorded here so the cap this
 * tenant is subject to is visible in its own repo. */
export const databaseMaxUserConnections = config.getNumber('databaseMaxUserConnections');

export const mediaEndpoint = config.require('mediaEndpoint');
export const mediaRegion = config.require('mediaRegion');
export const mediaBucket = config.require('mediaBucket');
export const mediaTenantPrefix = config.require('mediaTenantPrefix');
export const mediaPublicBaseUrl = config.require('mediaPublicBaseUrl');

/**
 * Both halves of the Object Storage key pair are secret config, including the
 * key id.
 *
 * The id is not itself a secret. Holding the pair together is what makes
 * rotating this credential one edit instead of two, and a rotation that updates
 * one half is a tenant whose media stops working with a 403 that reads as a
 * bucket-policy problem.
 */
export const mediaAccessKeyId = config.requireSecret('mediaAccessKeyId');
export const mediaSecretAccessKey = config.requireSecret('mediaSecretAccessKey');

/** The single number every upload-related limit derives from, in MiB. Left
 * unset, the component's own default applies. */
export const uploadCeilingMib = config.getNumber('uploadCeilingMib');
export const rssBudgetMib = config.getNumber('rssBudgetMib');

/**
 * Optional mail, all-or-nothing. `get`, not `require`: a tenant that does not
 * send mail sets nothing here and the component receives no mail block at all.
 * Once `mailHost` is set the rest is `require`d, so a half-configured block
 * fails at preview rather than rendering a secrets file Ghost cannot send with.
 */
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
