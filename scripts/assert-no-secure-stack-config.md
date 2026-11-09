# assert-no-secure-stack-config.py

Refuses a tenant stack config, or an exported stack state, that holds a secret.

Since component `8.0.0` a tenant stack holds no secret value. The component
takes no `secrets` argument and exports `secretsEnvTemplate` (key names, empty
values) in place of the filled secrets file; the host fills it. What a stack
holds therefore cannot include a credential, and this is the check that says
so, so a `pulumi config set --secret` run out of habit does not put one back.

It is a sibling of `assert-no-committed-pulumi-secrets.py`, not an extension of
it. That script refuses an `encryptionsalt`, and its deploy-job caller reads its
exit status 1 as "a salt is committed". It deliberately allows a `secure:`
ciphertext with no salt beside it. Widening it would change what that exit
status means to a caller that branches on it.

## Usage

    assert-no-secure-stack-config.py PATH [PATH...]   # scan named stack configs
    assert-no-secure-stack-config.py --scan-tree DIR  # find them itself
    assert-no-secure-stack-config.py --state FILE...  # exported stack state
    assert-no-secure-stack-config.py --self-test

Exit status: 0 nothing held, 1 a secret is held, 3 a named file could not be
read. 3 wins over 1 when both happen: a tree with an unread file in it has not
been cleared. It prints a path, a line number and a key name. It never prints a
value, so a failing run does not publish the thing it found.

## What it refuses in a config

Reads lines of `Pulumi.<stack>.yaml`, skipping comments:

- a `secure:` entry, which is what `pulumi config set --secret` writes;
- the six keys this template read as secrets before 8.0.0 (`ownerEmail`,
  `databasePassword`, `mediaAccessKeyId`, `mediaSecretAccessKey`,
  `mailPassword`, `bulkEmailApiKey`), whatever sits under them. A plain value
  under one is a credential in cleartext, which is worse than a `secure:` one;
- any key whose name carries a credential word (`password`, `secret`, `token`,
  `apikey`, `accesskey`, `privatekey`, `credential`), so a new secret does not
  need the list above to be edited first.

The keys Pulumi writes itself (`secretsprovider`, `encryptedkey`,
`encryptionsalt`) are exempt. The salt is the other script's.

## What it refuses in a state

`pulumi stack export` writes a secret as an object carrying one signature key,
whether it holds the plaintext or the ciphertext. Any such object anywhere in
the document is a finding, reported as its JSON path. The passphrase provider's
`salt` is a plain string and is not one.

CI does not run `--state`. A tenant that last applied under component 7.x has
secret outputs in its checkpoint until its first apply under 8.0.0 replaces
them, so the check would fail that tenant's own migration. Run it by hand after
that apply:

    pulumi stack export --stack <slug> > state.json
    python3 scripts/assert-no-secure-stack-config.py --state state.json

The checkpoint holds ciphertext, not plaintext, but it is still delete-after-use.

## What it does not see

- a key inside an inline flow mapping (`config: {key: value}`);
- a stack config named `Pulumi.<stack>.yml` or `.json`, or whose case differs;
- a secret under a key with no credential word and not in the list, such as a
  plain `p:note: <password>`. The key name is the signal; a value is never read;
- a state exported with `--show-secrets`, which writes plaintext values and no
  signature. Do not export one.

## Tests (`test_assert_no_secure_stack_config.py`)

Every test targets a false pass: each former key, each quoting of `secure`, a
byte order mark on line one, an unreadable file reading as clean, a hostile key
forging a runner command, and a value reaching the report. The non-secret keys
this template reads are asserted to pass, so the check does not cry wolf on the
stack it protects. `--self-test` stays beside the runner because CI runs the
script rather than the test runner.
