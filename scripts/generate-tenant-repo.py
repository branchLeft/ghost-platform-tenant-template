#!/usr/bin/env python3
"""Turn a fresh clone of a template-generated repo into one tenant's repo.

Run by the platform's provisioning flow, from the root of the clone, once:

    generate-tenant-repo.py --slug blog

It renames the tenant-facing README over the template-facing one, substitutes
every `__LIKE_THIS__` placeholder, and then refuses if any placeholder survives
anywhere in the tree.

**It lives here rather than in the provisioning workflow on purpose.** The
substitution set is a property of the template — which files carry a
placeholder is decided by whoever edits this repo — and a copy of that list in
another repository's workflow goes stale the first time a placeholder is added
to a new file. The failure is silent and it propagates: the flow substitutes the
files it knows about, the generated repo ships the literal token in the one it
does not, and nothing downstream validates a Pulumi project name.

So the list is here, next to the files, and
`scripts/test_generate_tenant_repo.py` runs the whole generation against a copy
of this repo's own tree on every push.

Exit 0 on success, 1 on any refusal, 2 on usage error.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# Mirrors `validateTenantSlug` in @branchleft/ghost-platform-tenant. The slug
# becomes a Compose project, a systemd instance name, a directory, a MySQL
# identifier and two volume names, so a value valid in one and not another is a
# tenant that provisions and then cannot start.
SLUG = re.compile(r"\A[a-z][a-z0-9-]*\Z")

# MySQL caps an account name at 32 characters, and a tenant's database and its
# dedicated user share one name of `ghost_<slug>` -- so the slug has 26 to work
# with. Every other limit it meets is looser. The component is the authority;
# `test_generate_tenant_repo.py` asserts this copy still equals its
# `MAX_TENANT_SLUG_LENGTH`, because a copy that drifts upward fails open: a repo
# would be created for a slug whose stack then refuses at preview.
MAX_SLUG_LENGTH = 26

# An app host already runs a Compose stack under each of these names. A tenant
# taking one would overwrite that stack's directory, secrets file and systemd
# unit. Kept in step with `RESERVED_STACK_NAMES` in the component by
# `test_generate_tenant_repo.py`, which reads the installed package rather than
# trusting this copy.
RESERVED_SLUGS = ("website", "edge", "db", "monitoring")

PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")

# The tenant-facing README replaces the template-facing one, rather than being
# substituted in place: a generated repo's landing page should describe that
# tenant's stack, not call itself a template.
RENAMES = (("README.tenant.md", "README.md"),)

# Every file carrying a placeholder, **after** the rename above. Adding a
# placeholder to a file that is not in this list is the defect this script and
# its test exist to make impossible.
SUBSTITUTED_FILES = (
    "Pulumi.yaml",
    ".github/workflows/infra-ci.yml",
    "README.md",
)

# Files whose placeholder mentions are prose or pattern data rather than values
# to substitute. Checked by the final sweep, so this list is what stops that
# sweep failing on its own documentation.
SWEEP_EXCLUDED_DIRS = ("scripts", "graphify-out", ".claude", ".git", "node_modules")
COMMENT_LINE = re.compile(r"^\s*#")


class GenerateError(Exception):
    """Raised for anything a caller could have avoided, or that the tree refused."""


def validate_slug(slug: str) -> str:
    if not SLUG.match(slug):
        raise GenerateError(
            f"slug {slug!r} must start with a lowercase letter and contain only lowercase "
            "letters, digits and hyphens"
        )
    if len(slug) > MAX_SLUG_LENGTH:
        raise GenerateError(
            f"slug {slug!r} is {len(slug)} characters; the maximum is {MAX_SLUG_LENGTH} so that "
            "'ghost_' plus the slug fits MySQL's 32-character account-name limit"
        )
    if slug in RESERVED_SLUGS:
        raise GenerateError(
            f"slug {slug!r} is reserved -- an app host already runs a Compose stack of that name, "
            f"and a tenant using it would overwrite that stack's directory, secrets file and "
            f"systemd unit. Reserved: {', '.join(RESERVED_SLUGS)}"
        )
    return slug


def substitutions(slug: str) -> dict[str, str]:
    return {
        # The Pulumi project name. Every tenant stack shares one state bucket
        # and the object path derives from this, so it must be unique.
        "__TENANT_PULUMI_PROJECT__": f"{slug}-infra",
        "__TENANT_NAME__": slug,
    }


def apply_renames(root: pathlib.Path) -> list[str]:
    actions = []
    for source, target in RENAMES:
        src = root / source
        if not src.is_file():
            raise GenerateError(
                f"{source} is missing from this tree. A template snapshot without it cannot be "
                "generated from -- nothing else produces the tenant-facing README."
            )
        src.replace(root / target)
        actions.append(f"renamed {source} -> {target}")
    return actions


def apply_substitutions(root: pathlib.Path, slug: str) -> list[str]:
    replacements = substitutions(slug)
    actions = []
    for name in SUBSTITUTED_FILES:
        path = root / name
        if not path.is_file():
            raise GenerateError(
                f"{name} is named in SUBSTITUTED_FILES but is missing from this tree. Refusing "
                "rather than skipping: a file that has moved is a file whose placeholders stop "
                "being substituted, silently."
            )
        text = path.read_text(encoding="utf-8")
        for token, value in replacements.items():
            text = text.replace(token, value)
        path.write_text(text, encoding="utf-8")
        actions.append(f"substituted {name}")
    return actions


def sweep(root: pathlib.Path) -> list[str]:
    """Return every `path:line` outside the excluded dirs still carrying a token.

    A `#` comment line naming a placeholder is documenting it rather than
    carrying one, and a placeholder in a comment is substituted harmlessly where
    the file is substituted at all -- so those are not findings.
    """
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] in SWEEP_EXCLUDED_DIRS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if COMMENT_LINE.match(line):
                continue
            found = PLACEHOLDER.findall(line)
            if found:
                findings.append(f"{relative}:{number}: {', '.join(sorted(set(found)))}")
    return findings


def generate(root: pathlib.Path, slug: str) -> list[str]:
    validate_slug(slug)
    actions = apply_renames(root)
    actions.extend(apply_substitutions(root, slug))
    findings = sweep(root)
    if findings:
        raise GenerateError(
            "placeholders survived generation in files SUBSTITUTED_FILES does not name:\n  "
            + "\n  ".join(findings)
            + "\nAdd each file to SUBSTITUTED_FILES in this script and to README.md's "
            "placeholder table."
        )
    actions.append("no placeholder survives anywhere in the tree")
    return actions


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--root",
        default=".",
        help="tree to generate in place (default: the current directory)",
    )
    args = parser.parse_args(argv)
    try:
        for action in generate(pathlib.Path(args.root), args.slug):
            print(action)
    except GenerateError as exc:
        print(f"::error::generate-tenant-repo: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
