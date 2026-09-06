#!/usr/bin/env python3
"""Refuse a credential presented as a paste-and-run inline assignment.

`export SOME_PASSWORD='<placeholder>'` and `export SOME_PASSWORD='…'` both
read as copy-pasteable instructions, and both fail the same way: pasted
verbatim, the variable is set to the literal placeholder text rather than a
real credential. That failure is silent -- no error, nothing distinguishes it
from a working assignment -- and it surfaces later, somewhere else, as an
unexplained auth failure or (worse) a malformed value appended to a committed
config file.

The fix this guard enforces is not "add a comment" -- it is: never type a
credential after an `=`. Read it into a variable with a prompt that does not
echo (`read -rs`, one line, pasted alone) and export that instead. A runbook
following that shape never has a literal secret, real or placeholder, sitting
in an `export NAME=...` or a `printf ... >>` line in the first place.

Two placeholder shapes both have to be caught, not just the obvious one: the
angle-bracket form (`<the value>`) and the ellipsis form (`'…'`), because a
token-shape guard that only knows about `<...>` misses the second one
entirely -- proven by `ghost-tenant-blog`'s README, which used it.

Scope: markdown files only (RUNBOOK*.md, README*.md, *.md generally) -- this
is a guard on instructions meant to be pasted into a shell, not on committed
config or code, which `assert-no-committed-pulumi-secrets.py` already covers.

Usage:
    assert-no-inline-credential.py [file ...]

Exit status 0 if no file carries the pattern, 1 if any does.
"""

from __future__ import annotations

import pathlib
import re
import sys

# Names that mark an assignment as carrying a credential rather than an
# address, a slug, or any other value that is meant to fail loudly rather
# than silently when left unsubstituted. Deliberately narrower than "any
# placeholder" -- `export TENANT_SLUG=<slug>` is the address half of the
# owner's principle, already handled elsewhere, and flagging it here would
# just teach people to ignore this guard.
CREDENTIAL_NAME_PATTERN = re.compile(
    r"(PASSWORD|PASSPHRASE|SECRET|ACCESS_KEY|PRIVATE_KEY|TOKEN|CREDENTIAL|ENCRYPTIONSALT)",
    re.IGNORECASE,
)

# A trailing `# comment` is common on exactly this line shape (`... # from
# the password manager`) and must not hide the assignment from the anchor.
EXPORT_ASSIGNMENT = re.compile(
    r'^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([\'"])(.*?)\2\s*(#.*)?$'
)

# Every single- or double-quoted token on a line, in order. Used for `printf`
# lines: the first quoted token is the format string, the rest are its
# arguments -- either can carry the credential keyword or the placeholder.
QUOTED_TOKEN = re.compile(r"(['\"])(.*?)\1")

ELLIPSIS_RUN = re.compile(r"^\.{3,}$")


def is_placeholder(value: str) -> bool:
    """A value that is not a real credential, in either shape this estate
    has actually shipped: `<prose describing what goes here>` or a bare
    ellipsis (`...` or the Unicode `…`). Deliberately not "any short string"
    -- a real secret can look like anything, so this only ever flags the
    shapes a human writes when they mean "fill this in", never a false
    positive on an actual pasted value.
    """
    value = value.strip()
    if not value:
        return False
    if value.startswith("<") and value.endswith(">") and len(value) > 2:
        return True
    if "…" in value:  # the Unicode ellipsis character, '…'
        return True
    if ELLIPSIS_RUN.match(value):
        return True
    return False


def find_offenses(path: pathlib.Path) -> list[str]:
    offenses: list[str] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = EXPORT_ASSIGNMENT.match(line)
        if m:
            name, _, value, _ = m.groups()
            if CREDENTIAL_NAME_PATTERN.search(name) and is_placeholder(value):
                offenses.append(
                    f"{path}:{lineno}: `export {name}=...` assigns a "
                    f"placeholder inline ({value!r}). Read it instead: "
                    f'`read -rs {name}` on its own line, then `export {name}` '
                    "-- see RUNBOOK-bootstrap.md for the shape."
                )
            continue

        if "printf" not in line:
            continue
        tokens = [content for _, content in QUOTED_TOKEN.findall(line)]
        if len(tokens) < 2:
            continue
        if not CREDENTIAL_NAME_PATTERN.search(line):
            continue
        for value in tokens[1:]:
            if is_placeholder(value):
                offenses.append(
                    f"{path}:{lineno}: `printf` writes a placeholder "
                    f"({value!r}) on a line naming a credential. Read the "
                    "value into a variable first (`read -rs`, its own line) "
                    "and pass that instead of a literal."
                )
                break
    return offenses


def main(argv: list[str]) -> int:
    files = argv[1:]
    if not files:
        print("::error::assert-no-inline-credential.py: no files given")
        return 2
    ok = True
    for name in files:
        path = pathlib.Path(name)
        if not path.is_file():
            print(f"::error::not a file: {name}")
            ok = False
            continue
        for offense in find_offenses(path):
            print(f"::error::{offense}")
            ok = False
    if ok:
        print(f"OK: no inline credential assignment in {len(files)} file(s).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
