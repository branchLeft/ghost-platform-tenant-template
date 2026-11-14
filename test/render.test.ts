import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import * as pulumi from '@pulumi/pulumi';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Loads `index.ts` under Pulumi's mock runtime with placeholder config, so the
 * component's `validateTenantStack()` and `render()` run on this program
 * exactly as a deploy would, with no engine, stack or cloud. The stack holds no
 * secret value, so every place it could record one is searched for a sentinel
 * planted in the keys the template used to read as secrets. See
 * config.md, "Since 8.0.0: secrets live on the host".
 */

const PROJECT = 'tenant-render-test';
const SLUG = 'example';
const SENTINEL = 'sentinel-4f7c1a93-not-a-real-credential';
const DIGEST = 'c'.repeat(64);
const PIN = `ghcr.io/example/ghost-tenant@sha256:${DIGEST}`;

/** What `image-pin.txt` holds for the test that loads the program. The file is
 * not committed in the template (a tenant's own pin is written into its repo),
 * so `config.ts`'s read of it is answered here and nothing else is touched. */
const pinFile = vi.hoisted(() => ({ content: '' }));

vi.mock('fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('fs')>();
  const readFileSync = ((target: unknown, ...rest: unknown[]) =>
    String(target).endsWith('image-pin.txt')
      ? pinFile.content
      : (actual.readFileSync as (...args: unknown[]) => unknown)(
          target,
          ...rest
        )) as typeof actual.readFileSync;
  return { ...actual, readFileSync };
});

/** The keys `config.ts` read as secrets before 8.0.0. A stack migrated halfway
 * still carries them, set as secret config, and the program must not surface a
 * single one. */
const FORMER_SECRET_KEYS = [
  'ownerEmail',
  'databasePassword',
  'mediaAccessKeyId',
  'mediaSecretAccessKey',
  'mailPassword',
  'bulkEmailApiKey',
];

/** Placeholder config in reserved `.example.test` names, one value per key
 * `config.ts` reads. */
function fixtureConfig(): Record<string, unknown> {
  return {
    slug: SLUG,
    siteUrl: `https://${SLUG}.platform-domain.example.test`,
    uid: 30021,
    appHostPrivateIp: '10.20.1.100',
    hostPort: 8111,
    hostPortB: 8112,
    healthPort: 8113,
    databaseHost: '10.20.1.20',
    mediaEndpoint: 'https://objects.example.test',
    mediaRegion: 'region-1',
    zones: {
      demoZone: 'demo-domain.example.test',
      platformZone: 'platform-domain.example.test',
      ownedDomains: ['demo-domain.example.test', 'platform-domain.example.test'],
      demoMailDomain: 'demo-mail.example.test',
    },
    backupEncryptionRecipient: 'age1qplaceholderrecipientforexample',
    mailHost: 'mx.example.test',
    mailUser: `${SLUG}@example.test`,
    bulkEmailDomain: 'example-mail.example.test',
    mailDkimSelector: 'bl',
    mailCeiling: 10000,
    mailEstateCeiling: 10000,
  };
}

/** The same config plus a sentinel under every former secret key, as secret
 * config: the state of a stack that was set up before 8.0.0. */
function legacyConfig(): Record<string, unknown> {
  return {
    ...fixtureConfig(),
    ...Object.fromEntries(FORMER_SECRET_KEYS.map((key) => [key, `${SENTINEL}-${key}`])),
  };
}

/** Every output the program may export. Anything else is a refusal to review. */
const ALLOWED_EXPORTS = [
  'appHost',
  'composeFile',
  'composeUnit',
  'databaseName',
  'databaseUser',
  'edgeRequestBodyMaxSize',
  'edgeSiteBlock',
  'ghostSettings',
  'identity',
  'image',
  'imageEnvFile',
  'imageEnvPath',
  'mediaBucket',
  'mediaPublicBaseUrl',
  'provisionScript',
  'secretKeys',
  'secretsEnvPath',
  'secretsEnvTemplate',
  'stackDirectory',
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
  pinFile.content = `${PIN}\n`;
  created = [];
  recorded = [];
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Loads the program with `config`, with `secretKeys` set as secret config,
 * capturing every registered resource and every map passed to
 * `registerOutputs()`, which the mocks never expose and which is what the stack
 * state stores. */
async function loadProgram(
  config: Record<string, unknown>,
  secretKeys: string[] = []
): Promise<Record<string, unknown>> {
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
    secretKeys.map((key) => `${PROJECT}:${key}`)
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

function unwrap<T>(input: pulumi.Input<T>): Promise<pulumi.Unwrap<T>> {
  return new Promise<pulumi.Unwrap<T>>((resolve) => {
    pulumi.output(input).apply((value) => {
      resolve(value);
      return value;
    });
  });
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

function carries(value: unknown): boolean {
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return text.includes(SENTINEL);
}

/** What the stack would leak: an output that is a Pulumi secret, or any
 * resolved value, registered output or resource input carrying the sentinel. */
async function leaks(): Promise<string[]> {
  await new Promise((resolve) => setTimeout(resolve, 0));
  const found: string[] = [];
  for (const [index, resource] of created.entries()) {
    if (carries(resource.inputs)) {
      found.push(`input:${resource.type}:${index}`);
    }
  }
  for (const { component, outputs } of recorded) {
    if (outputs === undefined) {
      continue;
    }
    for (const [key, { secret, value }] of await resolveAll(outputs)) {
      if (secret) {
        found.push(`secret-output:${component}.${key}`);
      }
      if (carries(value)) {
        found.push(`value:${component}.${key}`);
      }
    }
  }
  return found;
}

describe('the stack program under mocks', () => {
  it('builds, and its outputs resolve', async () => {
    const program = await loadProgram(fixtureConfig());
    expect(typeof program.composeFile).toBe('string');
    expect(program.composeFile as string).toContain('ghost-a');
    const identity = await unwrap(program.identity as pulumi.Output<{ databaseName: string }>);
    expect(identity.databaseName).toBe(`ghost_${SLUG}`);
  });

  it('exports the secrets file as key names alone, in plain', async () => {
    const program = await loadProgram(fixtureConfig());
    const template = await unwrap(program.secretsEnvTemplate as pulumi.Output<string>);
    const keys = await unwrap(program.secretKeys as pulumi.Output<string[]>);
    expect(keys).toEqual(
      expect.arrayContaining([
        'GHOST_DB_PASSWORD',
        'GHOST_OWNER_EMAIL',
        'GHOST_S3_SECRET_ACCESS_KEY',
      ])
    );
    for (const line of template.split('\n')) {
      expect(line === '' || line.startsWith('#') || /^[A-Z0-9_]+=$/.test(line)).toBe(true);
    }
    expect(await pulumi.isSecret(pulumi.output(program.secretsEnvTemplate as never))).toBe(false);
    expect('secretsEnvFile' in program).toBe(false);
  });

  it('exports nothing outside the reviewed list, and no output is a Pulumi secret', async () => {
    const program = await loadProgram(fixtureConfig());
    expect(Object.keys(program).sort()).toEqual(ALLOWED_EXPORTS);
    const exported = await resolveAll(program);
    for (const [key, { secret }] of exported) {
      expect(secret, `${key} is a Pulumi secret`).toBe(false);
    }
  });

  it('takes the image from the pin file, not from Pulumi config', async () => {
    const exported = await loadProgram({
      ...fixtureConfig(),
      imageRef: 'ghcr.io/example/ignored@sha256:' + 'd'.repeat(64),
    });
    expect(exported['image']).toBe(PIN);
  });

  it('refuses a pin file that is not digest-pinned', async () => {
    pinFile.content = 'ghcr.io/example/ghost-tenant:latest\n';
    await expect(loadProgram(fixtureConfig())).rejects.toThrow(/imageRef must be digest-pinned/);
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

describe('the stack holds no secret value', () => {
  it('records no former secret key anywhere, though a half-migrated stack still carries them', async () => {
    const program = await loadProgram(legacyConfig(), FORMER_SECRET_KEYS);
    const exported = await resolveAll(program);
    expect(carries([...exported.values()].map((entry) => entry.value))).toBe(false);
    expect(await leaks()).toEqual([]);
  });

  it('reads no secret config and passes no secrets argument', () => {
    for (const file of ['config.ts', 'index.ts']) {
      // Comments say what is gone, so only code is searched.
      const source = readFileSync(resolve(__dirname, '..', file), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '');
      expect(source, `${file} reads secret config`).not.toMatch(/requireSecret|getSecret/);
      expect(source, `${file} passes secrets`).not.toMatch(/\bsecrets\s*:/);
      expect(source, `${file} exports the secret file`).not.toMatch(/secretsEnvFile/);
    }
  });

  // The control cases: a leaking stand-in must make the same search fail, so a
  // silent all-clear is not possible. Each plants the sentinel the way a
  // regression would and expects `leaks()` to name it.
  it('control: a secret output is found', async () => {
    await loadProgram(fixtureConfig());
    recorded.push({ component: 'Stand-in', outputs: { leaked: pulumi.secret('x') } });
    expect(await leaks()).toContain('secret-output:Stand-in.leaked');
  });

  it('control: a sentinel in a registered output is found', async () => {
    await loadProgram(fixtureConfig());
    recorded.push({ component: 'Stand-in', outputs: { leaked: SENTINEL } });
    expect(await leaks()).toContain('value:Stand-in.leaked');
  });

  it('control: a sentinel in a resource input is found', async () => {
    await loadProgram(fixtureConfig());
    created.push({ type: 'stand-in:Res', name: 'r', inputs: { leaked: SENTINEL } });
    expect((await leaks()).some((entry) => entry.startsWith('input:stand-in:Res'))).toBe(true);
  });
});
