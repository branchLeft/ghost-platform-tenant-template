import * as pulumi from '@pulumi/pulumi';

const config = new pulumi.Config();
const gcpConfig = new pulumi.Config('gcp');

export const projectId = gcpConfig.require('project');

/**
 * Platform-owned values this stack consumes and never creates.
 *
 * They were read from a `pulumi.StackReference` to the platform stack until
 * each tenant moved to its own state bucket: a stack reference cannot cross
 * backends, so a reference to a stack in another bucket resolves to
 * `unknown stack`. Plain config also removes the ordering coupling that
 * stopped a tenant stack previewing before the platform stack had been
 * applied.
 *
 * `require`, not `get`: an unset value must fail at preview rather than
 * deploy a revision pointed at nothing.
 */
export const platformDbInstanceConnectionName = config.require('platformDbInstanceConnectionName');
export const platformTenantImageRepositoryDockerPath = config.require(
  'platformTenantImageRepositoryDockerPath'
);
export const platformMediaBucketUrl = config.require('platformMediaBucketUrl');

/**
 * The deployer identity this repo's CI federates into. Created and federated
 * by the platform's provisioning flow, never by this program — a Pulumi
 * program cannot create the identity it runs as, and the roles needed to try
 * (`iam.serviceAccountAdmin`, `resourcemanager.projectIamAdmin`) are the ones
 * a deploy identity must never hold.
 *
 * Needed here only for the one binding in `index.ts` that cannot be made
 * before this stack's first apply: `actAs` on the tenant runtime service
 * account, whose email does not exist until `GhostTenant` creates it.
 */
export const deployerServiceAccountEmail = config.require('deployerServiceAccountEmail');
