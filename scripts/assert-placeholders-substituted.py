#!/usr/bin/env python3
"""Guard both directions of template placeholder substitution.

Usage: assert-placeholders-substituted.py [file ...] | --mangled [file ...] | --template
Exit status 0 if the relevant check passes, 1 if it does not. See
assert-placeholders-substituted.md for what each of the three checks means and
why two placeholders need it.
"""

import pathlib
import re
import sys

# The placeholder names, the files each one must appear in, and the pattern.
# Derived from scripts/placeholder_table.py, not kept by hand here.
from placeholder_table import DEFAULT_FILES, PLACEHOLDER_PATTERN, TEMPLATE_PLACEHOLDER_FILES

# Prettier's markdown formatter treats `__word__` as bold-emphasis syntax and
# rewrites it to `**word**` wherever it is not inside a code span -- so a
# placeholder living in prose, rather than in YAML, can be silently mangled
# into a form the substitution flow never matches. Restricted to the known
# names above, not `[A-Z][A-Z0-9_]*`, so ordinary bold markdown naming
# anything else -- `**Note**`, `**Warning**` -- is never mistaken for a
# corrupted placeholder. Bolding one of *these* names on purpose -- prose
# documenting the placeholder itself, e.g. in a bold heading -- is not
# distinguishable from Prettier's rewrite and fails the same way; there is no
# third state to tell "written as bold prose" from "mangled" apart by.
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
