#!/usr/bin/env python3
"""Fail if a `__LIKE_THIS__` template placeholder is still unsubstituted.

Every value in this repo that must be unique per generated tenant repo is
committed as a `__SOME_NAME__` placeholder (see README.md's placeholder
table), meant to be substituted by the provisioning script when the repo is
generated from this template.

Two remain. `__TENANT_NAME__` is validated downstream -- the `--stack` flag
errors with "stack not found". The Pulumi *project* name in Pulumi.yaml is
not: nothing sends it to a GCP API, and Pulumi's own project-name grammar
(alphanumerics, hyphens, underscores, periods) accepts the placeholder text
unchanged, so an unsubstituted one produces a valid-looking state object path
under a name no human chose. Each tenant now has its own state bucket, so the
old cross-tenant collision is gone; what is left is a stack filed under a
name nobody will recognise at restore time.

Run as a CI preflight, before anything is applied -- the type-check job
(can't be skipped by an operator following the runbook) and the deploy
job's delete-guard preflight (the last gate before `pulumi up`) both call
this.

Usage:
    assert-placeholders-substituted.py [file ...]

With no arguments, checks every file the README's placeholder table names.
Exit status 0 if no placeholder remains, 1 if one does.
"""

import pathlib
import re
import sys

PLACEHOLDER_PATTERN = re.compile(r"__[A-Z][A-Z0-9_]*__")

DEFAULT_FILES = [
    "Pulumi.yaml",
    ".github/workflows/infra-ci.yml",
    "scripts/assert-no-tenant-deletes.py",
]


def find_placeholders(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return sorted(set(PLACEHOLDER_PATTERN.findall(text)))


def main(argv: list[str]) -> int:
    files = argv[1:] or DEFAULT_FILES
    failed = False
    for name in files:
        path = pathlib.Path(name)
        if not path.is_file():
            print(f"::error::not a file: {name}")
            failed = True
            continue
        found = find_placeholders(path)
        if found:
            print(
                f"::error::{name} still contains unsubstituted template "
                f"placeholder(s): {', '.join(found)}. The provisioning "
                "script substitutes these when generating a tenant repo "
                "from this template -- see README.md's placeholder table. "
                "Nothing downstream validates Pulumi.yaml's project name, so "
                "an unsubstituted one applies cleanly under a name nobody "
                "chose."
            )
            failed = True
    if not failed:
        print(f"OK: no unsubstituted placeholders in {len(files)} file(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
