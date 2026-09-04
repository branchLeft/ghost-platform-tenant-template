#!/usr/bin/env python3
"""Guard both directions of template placeholder substitution.

Every value in this repo that must be unique per generated tenant repo is
committed as a `__SOME_NAME__` placeholder (see README.md's placeholder
table), meant to be substituted by the provisioning flow when the repo is
generated from this template. Three checks live here because the correct
state of a placeholder is a *different* fact in each place this repo's CI
runs:

    (no flag)   a generated tenant repo: no placeholder may remain, intact or
                mangled. Exit 1 if one does.
    --mangled   any repo, unconditionally: no known placeholder may appear in
                Prettier's bold-emphasis rewrite of it, `**NAME**` in place of
                `__NAME__` -- see .prettierignore's comment for how that
                happens. A mangled placeholder means substitution silently
                found nothing to replace, in the template *or* in a repo
                already generated from it, so this check is never gated on
                whether the repo is the template.
    --template  the template repo itself: every known placeholder must still
                be present, intact, in the file(s) README.md's table names for
                it. An unsubstituted placeholder is this repo's correct,
                permanent state -- the opposite of the no-flag check above --
                so running the no-flag check here would fail a clean build.

Two placeholders remain. `__TENANT_NAME__` is validated downstream -- the
`--stack` flag errors with "stack not found". The Pulumi *project* name in
Pulumi.yaml is not: nothing sends it to an API, and Pulumi's own project-name
grammar (alphanumerics, hyphens, underscores, periods) accepts the placeholder
text unchanged, so an unsubstituted one produces a valid-looking state object
path under a name no human chose. Every tenant stack now shares one state
bucket, so that is worse than it was when each had its own: two tenants
generated without substitution would collide on the same object path.

Usage:
    assert-placeholders-substituted.py [file ...]
    assert-placeholders-substituted.py --mangled [file ...]
    assert-placeholders-substituted.py --template

Exit status 0 if the relevant check passes, 1 if it does not.
"""

import pathlib
import re
import sys

PLACEHOLDER_PATTERN = re.compile(r"__[A-Z][A-Z0-9_]*__")

# Kept in step with README.md's placeholder table by hand. A file dropped from
# here is a file the check stops covering, silently -- which is why the table
# and this list are both named in the pull-request checklist for any change
# that adds a placeholder.
DEFAULT_FILES = [
    "Pulumi.yaml",
    ".github/workflows/infra-ci.yml",
]

# Every placeholder name the table carries, and the file(s) each must appear
# in, intact, while this repo is still the template. Absent from
# DEFAULT_FILES above because generation renames it to README.md before
# substituting it -- it does not exist, under this name, in a generated repo.
TEMPLATE_PLACEHOLDER_FILES: dict[str, list[str]] = {
    "TENANT_PULUMI_PROJECT": ["Pulumi.yaml"],
    "TENANT_NAME": [".github/workflows/infra-ci.yml", "README.tenant.md"],
}

# Prettier's markdown formatter treats `__word__` as bold-emphasis syntax and
# rewrites it to `**word**` wherever it is not inside a code span -- so a
# placeholder living in prose, rather than in YAML, can be silently mangled
# into a form the substitution flow never matches. Restricted to the known
# names above, not `[A-Z][A-Z0-9_]*`, so ordinary bold markdown --
# `**Note**`, `**Warning**` -- is never mistaken for a corrupted placeholder.
MANGLED_PATTERN = re.compile(
    r"\*\*(?:" + "|".join(TEMPLATE_PLACEHOLDER_FILES) + r")\*\*"
)


def find_placeholders(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return sorted(set(PLACEHOLDER_PATTERN.findall(text)))


def find_mangled(path: pathlib.Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return sorted(set(MANGLED_PATTERN.findall(text)))


def check_substituted(files: list[str]) -> bool:
    """No placeholder, intact or mangled, in any of `files`. The check for a
    generated tenant repo, where both forms are equally a bug: an intact one
    was never substituted, a mangled one was substituted against a token
    Prettier had already rewritten out from under it.
    """
    ok = True
    for name in files:
        path = pathlib.Path(name)
        if not path.is_file():
            print(f"::error::not a file: {name}")
            ok = False
            continue
        found = find_placeholders(path)
        if found:
            print(
                f"::error::{name} still contains unsubstituted template "
                f"placeholder(s): {', '.join(found)}. The provisioning "
                "flow substitutes these when generating a tenant repo "
                "from this template -- see README.md's placeholder table. "
                "Nothing downstream validates Pulumi.yaml's project name, so "
                "an unsubstituted one applies cleanly under a name nobody "
                "chose."
            )
            ok = False
        mangled = find_mangled(path)
        if mangled:
            print(
                f"::error::{name} contains a Prettier-mangled placeholder: "
                f"{', '.join(mangled)}. Substitution matches `__NAME__` "
                "exactly, so a token already rewritten to `**NAME**` was "
                "never replaced -- the literal bold-markdown word shipped "
                "into this repo instead."
            )
            ok = False
    return ok


def check_mangled(files: list[str]) -> bool:
    """No known placeholder appears in Prettier-mangled form in `files`. Runs
    unconditionally -- in the template and in a generated repo alike, because
    a mangled placeholder is a bug in both.
    """
    ok = True
    for name in files:
        path = pathlib.Path(name)
        if not path.is_file():
            # A missing file is check_substituted's or check_template's
            # concern; --mangled's default file list includes one
            # (README.tenant.md) that correctly does not exist post-generation.
            continue
        mangled = find_mangled(path)
        if mangled:
            print(
                f"::error::{name} contains a Prettier-mangled placeholder: "
                f"{', '.join(mangled)}. Markdown's bold-emphasis syntax is "
                "also `__word__`, so a formatter rewrites an unquoted "
                "`__NAME__` placeholder into `**NAME**` and the provisioning "
                "substitution then finds nothing to replace -- see "
                ".prettierignore's comment."
            )
            ok = False
    return ok


def check_template(mapping: dict[str, list[str]] | None = None) -> bool:
    """Every known placeholder is present, intact, in the file(s) it belongs
    in. The inverse of check_substituted: correct only in the template
    itself, where an unsubstituted placeholder is the permanent, intended
    state, so its *absence* -- substituted away, or mangled beyond
    recognition -- is the bug.

    `mapping` defaults to TEMPLATE_PLACEHOLDER_FILES; a test passes its own so
    it can exercise a failure without touching this repo's own tracked files.
    """
    if mapping is None:
        mapping = TEMPLATE_PLACEHOLDER_FILES
    ok = True
    contents: dict[str, list[str]] = {}
    for names in mapping.values():
        for name in names:
            if name in contents:
                continue
            path = pathlib.Path(name)
            if not path.is_file():
                print(f"::error::not a file: {name}")
                contents[name] = []
                ok = False
                continue
            contents[name] = find_placeholders(path)

    for placeholder, names in mapping.items():
        token = f"__{placeholder}__"
        for name in names:
            if name not in contents:
                continue
            if token not in contents[name]:
                print(
                    f"::error::{name} no longer carries {token}, intact. "
                    "README.md's placeholder table names this file as one "
                    "that must always carry it while this repo is the "
                    "template -- either the provisioning substitution ran "
                    "here by mistake, or something (Prettier, an editor's "
                    "format-on-save) rewrote it into bold-emphasis markdown."
                )
                ok = False
    return ok


def main(argv: list[str]) -> int:
    args = argv[1:]

    if args and args[0] == "--template":
        if len(args) > 1:
            print("::error::--template takes no file arguments")
            return 2
        ok = check_template()
        if ok:
            print("OK: the template still carries every known placeholder, intact.")
        return 0 if ok else 1

    if args and args[0] == "--mangled":
        files = args[1:] or DEFAULT_FILES + [
            name
            for names in TEMPLATE_PLACEHOLDER_FILES.values()
            for name in names
            if name not in DEFAULT_FILES
        ]
        ok = check_mangled(files)
        if ok:
            print(f"OK: no Prettier-mangled placeholders in {len(files)} file(s).")
        return 0 if ok else 1

    files = args or DEFAULT_FILES
    ok = check_substituted(files)
    if ok:
        print(f"OK: no unsubstituted placeholders in {len(files)} file(s).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
