import * as pulumi from '@pulumi/pulumi';
import * as gcp from '@pulumi/gcp';
import { GhostTenant } from '@branchleft/ghost-platform-tenant';
import {
  projectId,
  deployerServiceAccountEmail,
  platformDbInstanceConnectionName,
  platformTenantImageRepositoryDockerPath,
  platformMediaBucketUrl,
} from './config';

const config = new pulumi.Config();
const tenantName = config.require('tenantName');
const siteUrl = config.require('siteUrl');

// `require`, not defaulted: an unset value must fail loudly at
// preview/up rather than deploy a Cloud Run revision against a
// placeholder image.
const imageDigestOrTag = config.require('imageDigestOrTag');

// `get`, not `require`: a tenant that doesn't send mail yet sets nothing
// here, and `mail` below stays `undefined`. Once `mailHost` is set, the
// rest of the block is `require`d — a half-configured mail block should
// fail at preview, not deploy a revision that silently can't send mail.
const mailHost = config.get('mailHost');
const mail = mailHost
  ? {
      smtpHost: mailHost,
      smtpPort: config.get('mailPort'),
      smtpUser: config.require('mailUser'),
      smtpPassword: config.requireSecret('mailSmtpPassword'),
      from: config.require('mailFrom'),
    }
  : undefined;

// `get`, not `require`: a tenant that doesn't use bulk email yet sets
// nothing here, and `bulkEmail` below stays `undefined`. Once
// `bulkEmailBaseUrl` is set, the rest of the block is `require`d — a
// half-configured bulk-email block should fail at preview, not deploy a
// revision that silently can't send newsletters.
const bulkEmailBaseUrl = config.get('bulkEmailBaseUrl');
const bulkEmail = bulkEmailBaseUrl
  ? {
      baseUrl: bulkEmailBaseUrl,
      domain: config.require('bulkEmailDomain'),
      apiKey: config.requireSecret('bulkEmailApiKey'),
    }
  : undefined;

const tenant = new GhostTenant(tenantName, {
  tenantName,
  siteUrl,
  imageDigestOrTag,
  platform: {
    dbInstanceConnectionName: platformDbInstanceConnectionName,
    tenantImageRepositoryDockerPath: platformTenantImageRepositoryDockerPath,
    mediaBucketUrl: platformMediaBucketUrl,
  },
  mail,
  bulkEmail,
});

/**
 * Lets the deployer deploy Cloud Run revisions that run as this tenant's own
 * runtime service account — without it, the first apply 403s on
 * `iam.serviceaccounts.actAs` when it sets `template.serviceAccount`.
 *
 * Scoped to this one service account, never a project-wide
 * `roles/iam.serviceAccountUser`, which would let this deployer act as every
 * other tenant's deployer too.
 *
 * This is the one identity-shaped resource left in a tenant repo, and it is
 * here because it cannot be anywhere else: the runtime service account it
 * names does not exist until the line above creates it. Creating it needs
 * `iam.serviceaccounts.setIamPolicy` on that account, which the deployer does
 * not hold — so it lands on the provisioning identity's first apply and is
 * `same` on every CI run after that. A change that would recreate it fails
 * loudly in CI rather than being applied.
 */
export const deployerCanActAsTenantSa = new gcp.serviceaccount.IAMMember(
  `deployer-can-act-as-${tenantName}-sa`,
  {
    serviceAccountId: pulumi.interpolate`projects/${projectId}/serviceAccounts/${tenant.serviceAccountEmail}`,
    role: 'roles/iam.serviceAccountUser',
    member: `serviceAccount:${deployerServiceAccountEmail}`,
  }
);

export const cloudRunServiceName = tenant.cloudRunServiceName;
export const cloudRunServiceUri = tenant.cloudRunServiceUri;
export const tenantServiceAccountEmail = tenant.serviceAccountEmail;
export const databaseName = tenant.databaseName;
export const maxUserConnectionsStatement = tenant.maxUserConnectionsStatement;
