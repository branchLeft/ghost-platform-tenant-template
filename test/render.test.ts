import * as pulumi from '@pulumi/pulumi';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Loads `index.ts` under Pulumi's mock runtime with placeholder config, so
 * the component's `validateTenantStack()` and `render()` run on this program
 * exactly as a deploy would, with no engine, stack or cloud. A sentinel owner
 * address is set as secret config, and every place the program could record
 * it is searched: only the secrets file may carry it, as a secret. See
 * config.md#since-700-owneremail-is-secret.
 */

const PROJECT = 'tenant-render-test';
const SLUG = 'example';
const SENTINEL_LOCAL = 'owner-sentinel-4f7c1a93';
const SENTINEL = `${SENTINEL_LOCAL}@sentinel-owner.example.test`;
const DIGEST = 'c'.repeat(64);

/** Placeholder config in reserved `.example.test` names, one value per key
 * `config.ts` reads. Secrets are placeholders too. */
function fixtureConfig(): Record<string, unknown> {
  return {
    slug: SLUG,
    siteUrl: `https://${SLUG}.platform-domain.example.test`,
    uid: 30021,
    appHostPrivateIp: '10.20.1.100',
    hostPort: 8111,
    hostPortB: 8112,
    healthPort: 8113,
    ownerEmail: SENTINEL,
    imageRef: `ghcr.io/example/ghost-tenant@sha256:${DIGEST}`,
    databaseHost: '10.20.1.20',
    databasePassword: 'PLACEHOLDER_DB_PASSWORD',
    mediaEndpoint: 'https://objects.example.test',
    mediaRegion: 'region-1',
    mediaAccessKeyId: 'PLACEHOLDER_S3_KEY_ID',
    mediaSecretAccessKey: 'PLACEHOLDER_S3_SECRET',
    zones: {
      demoZone: 'demo-domain.example.test',
      platformZone: 'platform-domain.example.test',
      ownedDomains: ['demo-domain.example.test', 'platform-domain.example.test'],
      demoMailDomain: 'demo-mail.example.test',
    },
    backupEncryptionRecipient: 'age1qplaceholderrecipientforexample',
    mailHost: 'mx.example.test',
    mailUser: `${SLUG}@example.test`,
    mailPassword: 'PLACEHOLDER_MAIL_PASSWORD',
    bulkEmailDomain: 'example-mail.example.test',
    mailDkimSelector: 'bl',
    bulkEmailApiKey: 'PLACEHOLDER_BULK_KEY',
    mailCeiling: 10000,
    mailEstateCeiling: 10000,
  };
}

const SECRET_KEYS = [
  'ownerEmail',
  'databasePassword',
  'mediaAccessKeyId',
  'mediaSecretAccessKey',
  'mailPassword',
  'bulkEmailApiKey',
];

interface Created {
  type: string;
  name: string;
  inputs: Record<string, unknown>;
}

let created: Created[];
let recorded: { component: string; outputs: Record<string, unknown> | undefined }[];

beforeEach(() => {
  vi.resetModules();
  created = [];
  recorded = [];
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Loads the program with `config`, capturing every registered resource and
 * every map passed to `registerOutputs()`, which the mocks never expose and
 * which is what the stack state stores. */
async function loadProgram(config: Record<string, unknown>): Promise<Record<string, unknown>> {
  const sdk = await import('@pulumi/pulumi');
  sdk.runtime.setMocks(
    {
      newResource(args: pulumi.runtime.MockResourceArgs) {
        created.push({ type: args.type, name: args.name, inputs: args.inputs });
        return { id: `${args.name}-id`, state: { ...args.inputs } };
      },
      call() {
        return {};
      },
    },
    PROJECT,
    'test',
    false
  );
  sdk.runtime.setAllConfig(
    Object.fromEntries(
      Object.entries(config).map(([key, value]) => [
        `${PROJECT}:${key}`,
        typeof value === 'string' ? value : JSON.stringify(value),
      ])
    ),
    SECRET_KEYS.map((key) => `${PROJECT}:${key}`)
  );
  // `registerOutputs` is protected, so it is reached through a structural view.
  const prototype = sdk.ComponentResource.prototype as unknown as {
    registerOutputs(outputs?: unknown): void;
  };
  vi.spyOn(prototype, 'registerOutputs').mockImplementation(function (
    this: object,
    outputs?: unknown
  ) {
    recorded.push({
      component: this.constructor.name,
      outputs: outputs as Record<string, unknown> | undefined,
    });
  });
  return (await import('../index.js')) as Record<string, unknown>;
}

function unwrap<T>(output: pulumi.Output<T>): Promise<T> {
  return new Promise<T>((resolve) => {
    output.apply((value) => {
      resolve(value);
      return value;
    });
  });
}

function contains(value: unknown): boolean {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return text.toLowerCase().includes(SENTINEL_LOCAL);
}

/** Each value resolved, and whether Pulumi would store it as a secret. */
async function resolveAll(
  entries: Record<string, unknown>
): Promise<Map<string, { secret: boolean; value: unknown }>> {
  const out = new Map<string, { secret: boolean; value: unknown }>();
  for (const [key, raw] of Object.entries(entries)) {
    const output = pulumi.output(raw as pulumi.Input<unknown>);
    out.set(key, { secret: await pulumi.isSecret(output), value: await unwrap(output) });
  }
  return out;
}

/** Keys whose value carries the sentinel, failing any that carries it in plain. */
function carriers(resolved: Map<string, { secret: boolean; value: unknown }>): string[] {
  const found: string[] = [];
  for (const [key, { secret, value }] of resolved) {
    if (contains(value)) {
      found.push(key);
      expect(secret, `${key} carries the owner address in plain`).toBe(true);
    }
  }
  return found;
}

describe('the stack program under mocks', () => {
  it('builds, and its outputs resolve', async () => {
    const program = await loadProgram(fixtureConfig());
    expect(typeof program.composeFile).toBe('string');
    expect(program.composeFile as string).toContain('ghost-a');
    const secrets = await unwrap(program.secretsEnvFile as pulumi.Output<string>);
    expect(secrets).toContain('GHOST_DB_PASSWORD=PLACEHOLDER_DB_PASSWORD\n');
    const identity = await unwrap(program.identity as pulumi.Output<{ databaseName: string }>);
    expect(identity.databaseName).toBe(`ghost_${SLUG}`);
  });

  it('puts the owner address only in the secrets file, as a secret', async () => {
    const program = await loadProgram(fixtureConfig());
    const exported = await resolveAll(program);
    expect(exported.size).toBeGreaterThan(15);
    expect(carriers(exported)).toEqual(['secretsEnvFile']);
    expect(exported.get('secretsEnvFile')?.value).toContain(`GHOST_OWNER_EMAIL=${SENTINEL}\n`);
  });

  it('records the owner address in no registered output but the secrets file', async () => {
    await loadProgram(fixtureConfig());
    // The SDK also calls registerOutputs() once, empty, for every component.
    const tenant = recorded.filter(
      (call) => call.component === 'GhostTenant' && call.outputs !== undefined
    );
    expect(tenant).toHaveLength(1);
    const found: string[] = [];
    for (const { component, outputs } of recorded) {
      if (outputs === undefined) {
        continue;
      }
      const resolved = await resolveAll(outputs);
      if (component === 'GhostTenant') {
        expect(resolved.size).toBeGreaterThan(10);
      }
      found.push(...carriers(resolved).map((key) => `${component}.${key}`));
    }
    expect(found).toEqual(['GhostTenant.secretsEnvFile']);
  });

  it('records the owner address in no registered resource input', async () => {
    await loadProgram(fixtureConfig());
    await new Promise((resolve) => setTimeout(resolve, 0));
    const tenant = created.filter((r) => r.type === 'ghostPlatform:tenant:GhostTenant');
    expect(tenant).toHaveLength(1);
    expect(contains(created.map((r) => r.inputs))).toBe(false);
  });

  it('reads the owner address as secret config, never plain', async () => {
    await loadProgram(fixtureConfig());
    const config = (await import('../config.js')) as { ownerEmail: pulumi.Output<string> };
    expect(pulumi.Output.isInstance(config.ownerEmail)).toBe(true);
    expect(await pulumi.isSecret(config.ownerEmail)).toBe(true);
  });

  it('refuses a config with no mail transport, naming the missing key', async () => {
    const { mailHost: _omitted, ...mailless } = fixtureConfig();
    await expect(loadProgram(mailless)).rejects.toThrow(/mailHost/);
  });

  it('refuses a siteUrl outside the platform zone with no verified custom domain', async () => {
    const outside = { ...fixtureConfig(), siteUrl: 'https://example.elsewhere.example.test' };
    await expect(loadProgram(outside)).rejects.toThrow(/outside platform-domain\.example\.test/);
  });
});
