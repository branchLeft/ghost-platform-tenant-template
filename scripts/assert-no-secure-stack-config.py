#!/usr/bin/env python3
"""Refuse a tenant stack config, or an exported stack state, that holds a secret.

Usage: assert-no-secure-stack-config.py PATH... | --scan-tree DIR
       | --state FILE... | --self-test
Exit status: 0 nothing held, 1 a secret is held, 3 a named file could not be
read. See assert-no-secure-stack-config.md for why, and the shapes it does not see.

Reports a path, a line number and a key name. It never prints a value.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import re
import sys
import tempfile
from collections.abc import Iterator

# A mapping key at the start of a line, optionally quoted and optionally a list
# item. The key may itself contain colons (`project:name`), so it runs lazily up
# to the first colon that is followed by whitespace or the end of the line.
KEY_LINE = re.compile(r"""^\s*(?:-\s+)?(?P<q>["']?)(?P<key>[^\s"']+?)(?P=q)\s*:(?:\s|$)""")

# What Pulumi writes under a secret config key, in the three spellings a YAML
# parser reads as the same key.
SECURE_KEY = "secure"

# The keys this template used to read as secrets, and the words a credential's
# key carries. A plain value under either is a secret in cleartext, which is
# worse than a `secure:` one, so the name is refused whatever sits under it.
FORMER_SECRET_KEYS = frozenset(
    {
        "ownerEmail",
        "databasePassword",
        "mediaAccessKeyId",
        "mediaSecretAccessKey",
        "mailPassword",
        "bulkEmailApiKey",
    }
)
# Keys Pulumi itself writes at the top of a stack config. `secretsprovider` names
# the provider and holds no secret; the salt is another guard's.
PULUMI_OWN_KEYS = frozenset({"secretsprovider", "encryptionsalt", "encryptedkey"})
SECRET_WORDS = re.compile(
    r"password|passwd|secret|token|apikey|api_key|accesskey|access_key|privatekey|private_key|credential",
    re.IGNORECASE,
)

# Pulumi serialises a secret in a checkpoint as an object carrying this
# signature key, whether it holds the plaintext or the ciphertext.
STATE_SECRET_SIG_KEY = "4dabf18193072939515e22adb298388d"

# Written as an escape: the literal character is invisible in a diff.
BOM = "﻿"

# A stack config, not a project file: `Pulumi.yaml` declares the project.
STACK_CONFIG = re.compile(r"^Pulumi\.[^/]+\.yaml$")

SKIP_DIRS = {".git", ".worktrees", "node_modules", "graphify-out", "dist", "bin", "vendor"}

EXIT_SECRET_HELD = 1
# Not 2: argparse exits 2 on a usage error, and a caller that has to tell
# "could not read the file" from "could not understand the command line" cannot
# be handed the same number for both.
EXIT_UNREADABLE = 3


def is_commented(line: str) -> bool:
    """Whether the line is entirely a comment. Only a leading `#` counts."""
    return line.lstrip().startswith("#")


def key_of(line: str) -> str | None:
    """The mapping key a line starts, or None when it starts none."""
    match = KEY_LINE.match(line)
    return match.group("key") if match else None


def offending_lines(text: str) -> list[tuple[int, str]]:
    """Every (1-based line number, reason) that holds a secret. The reason names
    the key, never the value."""
    found: list[tuple[int, str]] = []
    # Stripped from the document, not from each line: U+FEFF is only a byte
    # order mark in the first position.
    text = text[len(BOM) :] if text.startswith(BOM) else text
    for number, line in enumerate(text.splitlines(), start=1):
        if is_commented(line):
            continue
        key = key_of(line)
        if key is None:
            continue
        if key == SECURE_KEY:
            found.append((number, "a `secure:` entry"))
            continue
        name = key.rsplit(":", 1)[-1]
        if name in PULUMI_OWN_KEYS:
            continue
        if name in FORMER_SECRET_KEYS or SECRET_WORDS.search(name):
            found.append((number, f"the key `{name}`"))
    return found


def state_findings(node: object, path: str = "$") -> Iterator[str]:
    """The JSON path of every object in an exported state that is a Pulumi secret."""
    if isinstance(node, dict):
        if STATE_SECRET_SIG_KEY in node:
            yield path
        for key, child in node.items():
            safe = re.sub(r"[^A-Za-z0-9_.:$/@+-]", "?", str(key))
            yield from state_findings(child, f"{path}.{safe}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from state_findings(child, f"{path}[{index}]")


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


def check_configs(paths: list[pathlib.Path]) -> int:
    held = False
    unreadable = False
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        # UnicodeDecodeError is a ValueError, not an OSError, so it is named.
        except (OSError, UnicodeDecodeError) as exc:
            # Not a pass: a file this cannot read is a file it cannot clear.
            print(f"::error::cannot read {path}: {exc}", file=sys.stderr)
            unreadable = True
            continue
        for number, reason in offending_lines(text):
            print(f"::error file={path},line={number}::stack config holds a secret: {reason}", file=sys.stderr)
            held = True
    return finish(held, unreadable, "config")


def check_states(paths: list[pathlib.Path]) -> int:
    held = False
    unreadable = False
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            print(f"::error::cannot read {path}: {type(exc).__name__}", file=sys.stderr)
            unreadable = True
            continue
        for where in state_findings(document):
            print(f"::error file={path}::stack state holds a secret at {where}", file=sys.stderr)
            held = True
    return finish(held, unreadable, "state")


def finish(held: bool, unreadable: bool, what: str) -> int:
    if held:
        print(
            f"\nA tenant stack's {what} holds no secret. The host renders the secrets file "
            "(see README.md, 'Secrets live on the host'); remove the entry above.",
            file=sys.stderr,
        )
    # Reported ahead of a finding: a tree holding a file nobody could read has
    # not been cleared, whatever else was found in the files that did read.
    if unreadable:
        return EXIT_UNREADABLE
    if held:
        return EXIT_SECRET_HELD
    return 0


# --------------------------------------------------------------------------
# Self-test: hermetic, fixtures in this file and a temp directory. Every value
# below is an obvious stand-in.
# --------------------------------------------------------------------------

CLEAN = "config:\n  proj:slug: example\n  proj:mailUser: someone@example.test\n  proj:mailPort: 587\n"
SECURE_VALUE = "config:\n  proj:other:\n    secure: FAKE-CIPHERTEXT\n"
INDENTED_SECURE = "config:\n  proj:other:\n      secure: FAKE-CIPHERTEXT\n"
QUOTED_SECURE = 'config:\n  proj:other:\n    "secure": FAKE-CIPHERTEXT\n'
LIST_SECURE = "config:\n  proj:other:\n    - secure: FAKE-CIPHERTEXT\n"
PLAIN_FORMER_KEY = "config:\n  proj:databasePassword: FAKE-PLAIN\n"
PLAIN_OWNER = "config:\n  proj:ownerEmail: someone@example.test\n"
QUOTED_FORMER_KEY = 'config:\n  "proj:mailPassword": FAKE-PLAIN\n'
NEW_SECRET_WORD = "config:\n  proj:stripeToken: FAKE-PLAIN\n"
BARE_FORMER_KEY = "databasePassword: FAKE-PLAIN\n"
COMMENTED = "# proj:databasePassword is no longer set here\n#   secure: FAKE\nconfig:\n  proj:slug: example\n"
SECURE_SUBSTRING_KEY = "config:\n  proj:secured: x\n  proj:insecure: y\n"
WORD_AS_VALUE = "config:\n  proj:slug: password-manager\n  proj:note: a secret garden\n"
SALT_ONLY = "config:\n  proj:slug: example\nencryptionsalt: v1:AAA=\n"
PULUMI_HEADER = "secretsprovider: passphrase\nencryptedkey: FAKE-WRAPPED-KEY\nconfig:\n  proj:slug: example\n"
BOM_SECURE = BOM + "secure: FAKE-CIPHERTEXT\nconfig:\n  proj:slug: example\n"

SECRET_STATE = {
    "version": 3,
    "deployment": {
        "resources": [
            {"type": "pulumi:pulumi:Stack", "outputs": {"secretsEnvFile": {STATE_SECRET_SIG_KEY: "FAKE-SIG", "ciphertext": "FAKE"}}},
        ]
    },
}
CLEAN_STATE = {
    "version": 3,
    "deployment": {
        "secrets_providers": {"type": "passphrase", "state": {"salt": "v1:AAA="}},
        "resources": [{"type": "pulumi:pulumi:Stack", "outputs": {"secretsEnvTemplate": "GHOST_DB_PASSWORD=\n"}}],
    },
}


def _quiet(call: object, paths: list[pathlib.Path]) -> tuple[int, str]:
    """`call` with its report captured, so a fixture's own error output does not
    read as a self-test failure."""
    buffer = io.StringIO()
    with contextlib.redirect_stderr(buffer):
        code = call(paths)  # type: ignore[operator]
    return code, buffer.getvalue()


def _self_test() -> int:
    cases = [
        ("a clean stack config passes", CLEAN, []),
        ("a secure: entry is caught", SECURE_VALUE, [3]),
        ("an indented secure: entry is caught", INDENTED_SECURE, [3]),
        ("a quoted secure key is caught", QUOTED_SECURE, [3]),
        ("a secure: list item is caught", LIST_SECURE, [3]),
        ("a plain value under a former secret key is caught", PLAIN_FORMER_KEY, [2]),
        ("the owner address key is caught", PLAIN_OWNER, [2]),
        ("a quoted former secret key is caught", QUOTED_FORMER_KEY, [2]),
        ("a new key with a credential word is caught", NEW_SECRET_WORD, [2]),
        ("a bare former secret key is caught", BARE_FORMER_KEY, [1]),
        ("commented-out mentions are ignored", COMMENTED, []),
        ("a key merely containing the word secure is not flagged", SECURE_SUBSTRING_KEY, []),
        ("a credential word in a value is not flagged", WORD_AS_VALUE, []),
        ("an encryptionsalt alone is another guard's, not this one's", SALT_ONLY, []),
        ("the secretsprovider and encryptedkey headers Pulumi writes pass", PULUMI_HEADER, []),
        ("a BOM does not hide a secure: entry", BOM_SECURE, [1]),
    ]
    failures = 0
    for label, text, expected in cases:
        actual = [number for number, _ in offending_lines(text)]
        if actual == expected:
            print(f"PASS: {label} -> lines {actual} (expected {expected})")
        else:
            print(f"FAIL: {label} -> lines {actual} (expected {expected})", file=sys.stderr)
            failures += 1

    reasons = [reason for _, reason in offending_lines(PLAIN_FORMER_KEY + SECURE_VALUE)]
    if reasons == ["the key `databasePassword`", "a `secure:` entry"] and not any("FAKE" in r for r in reasons):
        print("PASS: a finding names the key and never the value")
    else:
        print(f"FAIL: reasons {reasons!r}", file=sys.stderr)
        failures += 1

    state_cases = [
        ("a secret in exported state is caught", SECRET_STATE, 1),
        ("an exported state with no secret object passes", CLEAN_STATE, 0),
    ]
    for label, document, expected_count in state_cases:
        actual_count = len(list(state_findings(document)))
        if actual_count == expected_count:
            print(f"PASS: {label}")
        else:
            print(f"FAIL: {label} -> {actual_count} (expected {expected_count})", file=sys.stderr)
            failures += 1

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        (root / "Pulumi.tenant.yaml").write_text(CLEAN, encoding="utf-8")
        (root / "nested").mkdir()
        (root / "nested" / "Pulumi.tenant.yaml").write_text(SECURE_VALUE, encoding="utf-8")
        (root / "Pulumi.yaml").write_text("name: x\nruntime: nodejs\n", encoding="utf-8")
        skipped = root / "node_modules" / "pkg"
        skipped.mkdir(parents=True)
        (skipped / "Pulumi.fixture.yaml").write_text(SECURE_VALUE, encoding="utf-8")

        found = {p.relative_to(root).as_posix() for p in find_stack_configs(root)}
        expected_found = {"Pulumi.tenant.yaml", "nested/Pulumi.tenant.yaml"}
        if found == expected_found:
            print("PASS: --scan-tree finds the stack configs and skips Pulumi.yaml and node_modules")
        else:
            print(f"FAIL: --scan-tree found {sorted(found)}", file=sys.stderr)
            failures += 1

        code, report = _quiet(check_configs, find_stack_configs(root))
        if code == EXIT_SECRET_HELD and "nested/Pulumi.tenant.yaml" in report.replace("\\", "/"):
            print("PASS: a tree holding a secure: entry exits 1, naming the file")
        else:
            print(f"FAIL: secure tree -> exit {code}, report {report!r}", file=sys.stderr)
            failures += 1

        (root / "nested" / "Pulumi.tenant.yaml").write_text(CLEAN, encoding="utf-8")
        code, _ = _quiet(check_configs, find_stack_configs(root))
        if code == 0:
            print("PASS: a secret-free tree exits 0")
        else:
            print("FAIL: a secret-free tree did not exit 0", file=sys.stderr)
            failures += 1

        unreadable = root / "nested" / "Pulumi.bad.yaml"
        unreadable.write_bytes(b"\xff\xfe\x00")
        (root / "nested" / "Pulumi.tenant.yaml").write_text(SECURE_VALUE, encoding="utf-8")
        code, _ = _quiet(check_configs, find_stack_configs(root))
        if code == EXIT_UNREADABLE:
            print("PASS: an unreadable file exits 3, ahead of a finding")
        else:
            print(f"FAIL: unreadable tree -> exit {code}", file=sys.stderr)
            failures += 1
        unreadable.unlink()

        state_file = root / "state.json"
        state_file.write_text(json.dumps(SECRET_STATE), encoding="utf-8")
        code, report = _quiet(check_states, [state_file])
        if code == EXIT_SECRET_HELD and "FAKE" not in report:
            print("PASS: a state holding a secret exits 1 and prints no value")
        else:
            print(f"FAIL: secret state -> exit {code}, report {report!r}", file=sys.stderr)
            failures += 1
        state_file.write_text(json.dumps(CLEAN_STATE), encoding="utf-8")
        code, _ = _quiet(check_states, [state_file])
        if code == 0:
            print("PASS: a clean state exits 0")
        else:
            print(f"FAIL: clean state -> exit {code}", file=sys.stderr)
            failures += 1
        state_file.write_text("{not json", encoding="utf-8")
        code, _ = _quiet(check_states, [state_file])
        if code == EXIT_UNREADABLE:
            print("PASS: a state that is not JSON exits 3")
        else:
            print(f"FAIL: broken state -> exit {code}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=pathlib.Path, help="stack config files to scan")
    parser.add_argument("--scan-tree", type=pathlib.Path, metavar="DIR", help="find the stack configs under DIR")
    parser.add_argument("--state", nargs="+", type=pathlib.Path, metavar="FILE", help="exported stack state (JSON)")
    parser.add_argument("--self-test", action="store_true", help="run the built-in cases and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.state:
        return check_states(args.state)
    if args.scan_tree is not None:
        return check_configs(find_stack_configs(args.scan_tree))
    if not args.paths:
        parser.error("name a stack config, --scan-tree DIR, --state FILE or --self-test")
    return check_configs(args.paths)


if __name__ == "__main__":
    sys.exit(main())
