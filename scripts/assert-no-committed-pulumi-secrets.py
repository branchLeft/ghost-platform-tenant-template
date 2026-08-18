#!/usr/bin/env python3
"""Refuse a `Pulumi.<stack>.yaml` that carries an `encryptionsalt`.

The salt is an offline verifier for the stack passphrase: whoever holds it can
test candidate passphrases at their own rate, with nothing in the loop to
notice and no service to rate-limit them. It does not belong in a repository
anyone can clone.

A `secure:` ciphertext is deliberately *not* a finding. `branchLeft/standards`
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

Usage:

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

**What it does not see.** It reads lines, not YAML: a real parser is not
available here, the same stdlib-only constraint the other two scripts beside
this one work under. These shapes are missed:

- a key inside an inline flow mapping (`config: {encryptionsalt: x}`);
- a stack config named `Pulumi.<stack>.yml` or `Pulumi.<stack>.json`, both of
  which Pulumi accepts and `STACK_CONFIG` below does not match. The standards
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
"""

from __future__ import annotations

import argparse
import contextlib
import io
import pathlib
import re
import sys
import tempfile

# Anchored at the start of a mapping entry so the key has to *be*
# `encryptionsalt` rather than merely end with it, and case-insensitive because
# the raw bytes are an oracle whatever case wraps the key -- a leftover from a
# half-done migration is crackable even where Pulumi's own parser would skip
# the line. A guard that cries wolf is a guard people start passing
# --no-verify to, so it matches nothing wider than that.
#
# The quoted forms are matched too. Pulumi never writes one, but every YAML
# parser reads `"encryptionsalt":` as the same key, so an unquoted-only matcher
# clears a file that decrypts exactly as before.
FORBIDDEN_KEY = re.compile(
    r"""^\s*(?:-\s+)?(?:encryptionsalt|"encryptionsalt"|'encryptionsalt')\s*:""",
    re.IGNORECASE,
)

# Written as an escape, never as the literal character, which is invisible in
# every diff and editor that would have to review it.
#
# U+FEFF is not whitespace to Python -- `"\ufeff".isspace()` is False -- so a
# leading BOM puts a character in front of the anchor above that `^\s*` will
# never cross, and a BOM-prefixed salt reads as clean while Pulumi, whose
# parser drops the BOM, decrypts with it exactly as before. Nobody types one:
# a "UTF-8 with BOM" editor save produces it by accident.
BOM = "\ufeff"

# A stack config, not a project file: `Pulumi.yaml` declares the project and
# never holds the salt, while `Pulumi.<stack>.yaml` does.
STACK_CONFIG = re.compile(r"^Pulumi\.[^/]+\.yaml$")

SKIP_DIRS = {".git", ".worktrees", "node_modules", "graphify-out", "dist", "bin", "vendor"}

EXIT_SALT_FOUND = 1
# Not 2: argparse exits 2 on a usage error, and a caller that has to tell
# "could not read the file" from "could not understand the command line"
# cannot be handed the same number for both.
EXIT_UNREADABLE = 3


def is_commented(line: str) -> bool:
    """Whether the line is entirely a comment.

    Only a leading `#` counts. A `#` further along may be inside a value, and
    treating it as a comment marker would let `encryptionsalt: v1:x#y` through.
    """
    return line.lstrip().startswith("#")


def offending_lines(text: str) -> list[tuple[int, str]]:
    """Every (1-based line number, line) that commits a secret."""
    found = []
    # Stripped from the document, not from each line: U+FEFF is only a byte
    # order mark in the first position, and anywhere else it is a zero-width
    # no-break space that belongs to whatever value contains it.
    text = text[len(BOM) :] if text.startswith(BOM) else text
    for number, line in enumerate(text.splitlines(), start=1):
        if is_commented(line):
            continue
        if FORBIDDEN_KEY.search(line):
            found.append((number, line.rstrip()))
    return found


def is_stack_config(path: pathlib.Path) -> bool:
    return bool(STACK_CONFIG.match(path.name))


def find_stack_configs(root: pathlib.Path) -> list[pathlib.Path]:
    found = []
    for path in sorted(root.rglob("Pulumi.*.yaml")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if is_stack_config(path):
            found.append(path)
    return found


def check(paths: list[pathlib.Path]) -> int:
    found_salt = False
    unreadable = False
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        # UnicodeDecodeError is a ValueError, not an OSError, so it is named
        # here rather than covered by it. Uncaught it would leave the
        # interpreter exiting 1 on a traceback -- the same status as a
        # finding, which is precisely the distinction callers need.
        except (OSError, UnicodeDecodeError) as exc:
            # Not a pass. A file this cannot read is a file it cannot clear,
            # and reporting clean for it is the failure mode that matters.
            print(f"::error::cannot read {path}: {exc}", file=sys.stderr)
            unreadable = True
            continue
        for number, line in offending_lines(text):
            print(f"::error file={path},line={number}::{line}", file=sys.stderr)
            found_salt = True
    if found_salt:
        print(
            "\nRemove these before committing. The salt is supplied at deploy from a "
            "repository secret, and an operator applying by hand appends their own "
            "copy without committing it -- see README.md.",
            file=sys.stderr,
        )
    # Reported ahead of a finding: a tree holding a file nobody could read has
    # not been cleared, whatever else was found in the files that did read.
    if unreadable:
        return EXIT_UNREADABLE
    if found_salt:
        return EXIT_SALT_FOUND
    return 0


# --------------------------------------------------------------------------
# Self-test
#
# Hermetic: fixtures in this file and a temp directory, nothing about the real
# repository. It must pass in every state of the tree, so it can run on every
# edit to this script. test_assert_no_committed_pulumi_secrets.py beside it
# covers the same matcher from the outside; this stays because CI and the
# pre-commit hook invoke the script, not the test runner, and a check that can
# only be verified by a second tool is one that stops being verified.
# --------------------------------------------------------------------------

SALTED = "config:\n  gcp:project: p\nencryptionsalt: v1:AAA=:v1:BBB:CCC==\n"
INDENTED_SALT = "config:\n  a: b\n  encryptionsalt: v1:AAA=\n"
UPPERCASE_SALT = "config:\n  a: b\nEncryptionSalt: v1:AAA=\n"
LIST_SALT = "config:\n  proj:list:\n    - encryptionsalt: AAAA\n"
DOUBLE_QUOTED_SALT = 'config:\n  a: b\n"encryptionsalt": v1:AAA=\n'
SINGLE_QUOTED_SALT = "config:\n  a: b\n'encryptionsalt': v1:AAA=\n"
MISMATCHED_QUOTE = "config:\n  a: b\n\"encryptionsalt': v1:AAA=\n"
# The live shape of a generated tenant's stack config once it picks up a
# `secure:` config value: the ciphertext stays, the salt never lands. Both
# directions matter, so both are asserted on one fixture.
SECURE_VALUE = "config:\n  proj:token:\n    secure: AAAABBBBCCCC\n"
SECURE_VALUE_WITH_SALT = SECURE_VALUE + "encryptionsalt: v1:AAA=\n"
COMMENTED = (
    "# `encryptionsalt` is deliberately absent from this committed file.\n"
    "#     printf '\\nencryptionsalt: %s\\n' \"$SALT\" >> Pulumi.tenant.yaml\n"
    "config:\n  gcp:project: p\n"
)
SALT_SUFFIX_KEY = "config:\n  proj:noencryptionsalt: x\n  proj:encryptionsaltish: y\n"
QUOTED_SUFFIX_KEY = 'config:\n  "proj:noencryptionsalt": x\n'
CLEAN = "config:\n  gcp:project: branchleft-prod\n  proj:region: europe-west1\n"
SALT_WITH_HASH = "encryptionsalt: v1:AAA=#notacomment\n"
# A "UTF-8 with BOM" save of a salted file. The salt has to be on line 1 for
# these to test anything: that is the only line the BOM can hide behind.
BOM_SALT = BOM + "encryptionsalt: v1:AAA=\nconfig:\n  a: b\n"
BOM_COMMENTED = BOM + COMMENTED
BOM_CLEAN = BOM + CLEAN


def _quiet_check(paths: list[pathlib.Path]) -> tuple[int, str]:
    """`check` with its report captured, so a fixture's own error output does
    not read as a self-test failure."""
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = check(paths)
    return code, buffer.getvalue()


def _self_test() -> int:
    cases = [
        ("a committed salt is caught", SALTED, [3]),
        ("an indented salt is caught", INDENTED_SALT, [3]),
        ("a salt in any case is caught", UPPERCASE_SALT, [3]),
        ("a salt as a list item is caught", LIST_SALT, [3]),
        ("a double-quoted key is caught", DOUBLE_QUOTED_SALT, [3]),
        ("a single-quoted key is caught", SINGLE_QUOTED_SALT, [3]),
        ("a mismatched-quote key is not YAML and is not flagged", MISMATCHED_QUOTE, []),
        ("a secure: value with no salt beside it is allowed", SECURE_VALUE, []),
        ("a salt beside a secure: value is still caught", SECURE_VALUE_WITH_SALT, [4]),
        ("commented-out mentions are ignored", COMMENTED, []),
        ("a key merely containing the word is not flagged", SALT_SUFFIX_KEY, []),
        ("a quoted key merely containing the word is not flagged", QUOTED_SUFFIX_KEY, []),
        ("a clean stack config passes", CLEAN, []),
        ("a # inside a value does not make the line a comment", SALT_WITH_HASH, [1]),
        ("a BOM does not hide a salt on line 1", BOM_SALT, [1]),
        ("a BOM does not turn a comment into a finding", BOM_COMMENTED, []),
        ("a BOM on a clean file is not a finding", BOM_CLEAN, []),
    ]

    failures = 0
    for label, text, expected in cases:
        actual = [number for number, _ in offending_lines(text)]
        if actual == expected:
            print(f"PASS: {label} -> lines {actual} (expected {expected})")
        else:
            print(f"FAIL: {label} -> lines {actual} (expected {expected})", file=sys.stderr)
            failures += 1

    name_cases = [
        ("Pulumi.tenant.yaml", True),
        ("Pulumi.blog.yaml", True),
        ("Pulumi.yaml", False),
        ("Pulumi.tenant.yaml.bak", False),
        ("something.yaml", False),
    ]
    for name, expected in name_cases:
        actual = is_stack_config(pathlib.Path("a/b") / name)
        if actual == expected:
            print(f"PASS: {name} is{'' if expected else ' not'} a stack config")
        else:
            print(f"FAIL: {name} -> {actual} (expected {expected})", file=sys.stderr)
            failures += 1

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        (root / "Pulumi.tenant.yaml").write_text(CLEAN, encoding="utf-8")
        (root / "nested").mkdir()
        (root / "nested" / "Pulumi.tenant.yaml").write_text(SALTED, encoding="utf-8")
        (root / "Pulumi.yaml").write_text("name: x\nruntime: nodejs\n", encoding="utf-8")
        skipped = root / "node_modules" / "pkg"
        skipped.mkdir(parents=True)
        (skipped / "Pulumi.fixture.yaml").write_text(SALTED, encoding="utf-8")

        found = {p.relative_to(root).as_posix() for p in find_stack_configs(root)}
        expected_found = {"Pulumi.tenant.yaml", "nested/Pulumi.tenant.yaml"}
        if found == expected_found:
            print(f"PASS: --scan-tree finds {sorted(found)} and skips Pulumi.yaml and node_modules")
        else:
            print(f"FAIL: --scan-tree found {sorted(found)} (expected {sorted(expected_found)})", file=sys.stderr)
            failures += 1

        code, report = _quiet_check(find_stack_configs(root))
        if code == EXIT_SALT_FOUND and "nested/Pulumi.tenant.yaml" in report.replace("\\", "/"):
            print("PASS: a tree containing a salted stack config exits 1, naming the file")
        else:
            print(f"FAIL: salted tree -> exit {code}, report {report!r}", file=sys.stderr)
            failures += 1

        (root / "nested" / "Pulumi.tenant.yaml").write_text(CLEAN, encoding="utf-8")
        code, _ = _quiet_check(find_stack_configs(root))
        if code == 0:
            print("PASS: a salt-free tree exits 0")
        else:
            print("FAIL: a salt-free tree did not exit 0", file=sys.stderr)
            failures += 1

        # The string fixtures above prove the matcher; this proves the read
        # path hands it a BOM to strip rather than swallowing one silently.
        bom_file = root / "nested" / "Pulumi.bom.yaml"
        bom_file.write_bytes(b"\xef\xbb\xbf" + BOM_SALT[len(BOM) :].encode("utf-8"))
        code, report = _quiet_check([bom_file])
        if code == EXIT_SALT_FOUND:
            print("PASS: a real BOM-prefixed file on disk fails")
        else:
            print(f"FAIL: BOM-prefixed file -> exit {code}, report {report!r}", file=sys.stderr)
            failures += 1
        bom_file.unlink()

        code, _ = _quiet_check([root / "Pulumi.missing.yaml"])
        if code == EXIT_UNREADABLE:
            print("PASS: an unreadable path exits 3, not 0 and not a finding")
        else:
            print(f"FAIL: an unreadable path -> exit {code} (expected {EXIT_UNREADABLE})", file=sys.stderr)
            failures += 1

        undecodable = root / "Pulumi.binary.yaml"
        undecodable.write_bytes(b"config: x\n\xff\xfe not utf-8\n")
        code, _ = _quiet_check([undecodable])
        if code == EXIT_UNREADABLE:
            print("PASS: a config that is not valid UTF-8 exits 3, not a traceback")
        else:
            print(f"FAIL: undecodable config -> exit {code} (expected {EXIT_UNREADABLE})", file=sys.stderr)
            failures += 1
        undecodable.unlink()

    # This repo's own shape: a tree holding a project file and no stack config
    # at all. `--scan-tree` must report clean, not complain that it was given
    # nothing to do -- an argument error here would fail CI on every run in
    # the template and in a tenant whose config has not landed yet.
    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        (root / "Pulumi.yaml").write_text("name: x\nruntime: nodejs\n", encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = main(["--scan-tree", str(root)])
        if code == 0:
            print("PASS: --scan-tree over a tree with no stack config exits 0")
        else:
            print(f"FAIL: empty --scan-tree -> exit {code}, report {buffer.getvalue()!r}", file=sys.stderr)
            failures += 1

    if failures:
        print(f"\n{failures} self-test failure(s)", file=sys.stderr)
        return 1
    print("\nOK: assert-no-committed-pulumi-secrets.py self-test passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="stack config files to check")
    parser.add_argument("--scan-tree", metavar="DIR", help="find every Pulumi.<stack>.yaml under DIR")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    # Tested against what was asked for, not against what was found. A
    # `--scan-tree` that matches nothing is a clean tree, and treating it as a
    # usage error would turn the ordinary state of this repo into a CI failure.
    if not args.paths and not args.scan_tree:
        parser.error("pass at least one path, or --scan-tree DIR, or --self-test")

    targets = [pathlib.Path(p) for p in args.paths]
    if args.scan_tree:
        targets += find_stack_configs(pathlib.Path(args.scan_tree))
    return check(targets)


if __name__ == "__main__":
    sys.exit(main())
