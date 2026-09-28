# assert-no-committed-pulumi-secrets.py

Refuses a `Pulumi.<stack>.yaml` that carries an `encryptionsalt`.

The salt is an offline verifier for the stack passphrase: whoever holds it can
test candidate passphrases at their own rate, with nothing in the loop to
notice and no service to rate-limit them. It does not belong in a repository
anyone can clone.

A `secure:` ciphertext is deliberately _not_ a finding. `branchLeft/standards`
PUL-12 is explicit that a ciphertext with no salt beside it is not an oracle --
nothing in such a file lets an attacker derive the key or verify a guess
offline -- and that rejecting it would ban the safe half of the pattern with
the unsafe half for no security gain. Widening this matcher past the clause
would also make the local hook and the standards gate disagree, and the one
that fires first would be the one nobody believes.

This has to be a mechanical check rather than a rule people follow, because the
salt is not added by hand. Pulumi writes it back into the file itself, during
an ordinary `pulumi config set` or `pulumi stack init`, and the diff then looks
like exactly what the command was asked to do.

**This repo commits no stack config at all.** `Pulumi.<stack>.yaml` is written
into a generated tenant repo by the platform's provisioning flow, so
`--scan-tree` finds nothing here and everything in a tenant. That is the reason
the guard lives in the template: it has to travel with it to reach the repos
that do hold one. `--scan-tree` over a tree with no stack config is a pass, not
an argument error.

## Usage

    assert-no-committed-pulumi-secrets.py PATH [PATH...]   # scan named files
    assert-no-committed-pulumi-secrets.py --scan-tree DIR  # find them itself
    assert-no-committed-pulumi-secrets.py --self-test

Exit status is three-valued, because a caller that branches on "is a salt
committed here" has to be able to tell a no from an answer that was never
obtained: 0 nothing committed, 1 at least one salt committed, 3 at least one
named file could not be read. 3 wins over 1 when both happen -- a tree with an
unread file in it has not been cleared.

`--scan-tree` exists so CI does not inherit pre-commit's `files:` pattern as
its only definition of which files matter. A hook whose pattern silently stops
matching is a hook that passes everything, and the pattern lives in a different
file from this one.

## What it does not see

It reads lines, not YAML: a real parser is not available here, the same
stdlib-only constraint the other two scripts beside this one work under. These
shapes are missed:

- a key inside an inline flow mapping (`config: {encryptionsalt: x}`);
- a stack config named `Pulumi.<stack>.yml` or `Pulumi.<stack>.json`, both of
  which Pulumi accepts and `STACK_CONFIG` does not match. The standards
  gate's own scope regex has the same shape, so widening one without the other
  would only move the gap;
- a salt written inside a YAML comment. `is_commented` skips comment lines
  deliberately: a stack config on this pattern carries the re-append recipe as
  a comment, and flagging it would make the guard cry wolf on the file it
  exists to protect. Pulumi never writes a commented salt, and a commented one
  is inert to Pulumi's parser too -- but it is still readable by anyone
  cloning;
- a doubled byte order mark. The strip below removes one `U+FEFF`, so
  `BOM + BOM + salt` still sits in front of the anchor;
- a stack config whose filename differs in case (`pulumi.blog.yaml`).
  `STACK_CONFIG` is case-sensitive while macOS's filesystem is not, so such a
  file resolves for Pulumi and is skipped here;
- a key whose quotes do not match (`"encryptionsalt': v1:...`). No YAML parser
  accepts that either, so it is unreachable rather than merely unlikely.

Pulumi emits none of them -- it writes block style, unquoted keys and `.yaml`
throughout -- so each gap is between what Pulumi writes and what Pulumi would
accept, not a case anything here produces. They are listed because an
undisclosed gap in a security check is indistinguishable from an absent one.

## Tests (`test_assert_no_committed_pulumi_secrets.py`)

The guard has no second line of defence: a salt it clears is a salt that
reaches a tree anyone can clone, and no later gate looks for one. So the
failure shape every test is aimed at is a _false pass_ -- a key the matcher
does not recognise, a file the walker does not visit, a path it cannot read
and treats as clean, or an exit status that says nothing was found when
nothing was looked at.

The script's own `--self-test` covers the same matcher from the inside, and
both are kept: CI and the pre-commit hook invoke the script rather than this
runner, and a check whose only verification lives in a second tool is one that
stops being verified the first time the two are wired differently.
