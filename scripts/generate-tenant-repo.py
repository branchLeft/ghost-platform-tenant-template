#!/usr/bin/env python3
"""Turn a fresh clone of a template-generated repo into one tenant's repo.

Run once, from the root of the clone: generate-tenant-repo.py --slug blog
Exit 0 on success, 1 on any refusal, 2 on usage error. See
generate-tenant-repo.md for why removal order is reversed and why this lives
here rather than in the provisioning workflow.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys

# Mirrors `validateTenantSlug` in @branchleft/ghost-platform-tenant: the slug
# becomes a Compose project, systemd instance, directory, MySQL identifier and
# two volume names, so a value valid in one and not another is a tenant that
# provisions and then cannot start. The trailing character must be a letter or
# digit because the slug is also an S3-compatible media bucket name, and
# bucket naming requires that at both ends. See generate-tenant-repo.md.
SLUG = re.compile(r"\A[a-z]([a-z0-9-]*[a-z0-9])?\Z")

# MySQL caps an account name at 32 characters, and a tenant's database and its
# dedicated user share one name of `ghost_<slug>` -- so the slug has 26 to work
# with. Every other limit it meets is looser. The component is the authority;
# `test_generate_tenant_repo.py` asserts this copy still equals its
# `MAX_TENANT_SLUG_LENGTH`, because a copy that drifts upward fails open: a repo
# would be created for a slug whose stack then refuses at preview.
MAX_SLUG_LENGTH = 26

# Every name that is already taken -- an app host's own stacks and each
# existing tenant -- is in the tenant name register, `registers/tenant-names.json`
# in branchLeft/ghost-platform, the one home for that list. This script keeps no
# list of its own: it reads `tenant-names.json` beside it, a verbatim copy of the
# register, and `test_generate_tenant_repo.py` fails when the copy lags the
# register's published version. A tenant taking a taken name would overwrite
# that stack's directory, secrets file and systemd unit (or collide with an
# existing tenant's repository), so a registered name is refused whatever its
# kind -- tenant zero's included, which is not provisioned through here.
REGISTER_FILE = pathlib.Path(__file__).resolve().parent / "tenant-names.json"
REGISTER_KINDS = ("infrastructure", "tenant")

PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")

# Removed from the generated repo. Each one either asserts something only true
# of the template, or describes the template rather than the tenant.
TEMPLATE_ONLY_PATHS = (
    "scripts/test_generate_tenant_repo.py",
    "scripts/test_assert_placeholders_substituted.py",
    # The generator's copy of the tenant name register: read only by the script below.
    "scripts/tenant-names.json",
    # Describes this script, which is stripped below: shipped, it names a missing file.
    "scripts/generate-tenant-repo.md",
    # Last, and self-referential: this script is already loaded, so unlinking it
    # mid-run is safe on POSIX, but it is ordered last so that a failure leaves
    # the generator present for a re-run.
    "scripts/generate-tenant-repo.py",
)

# The tenant-facing README replaces the template-facing one, rather than being
# substituted in place: a generated repo's landing page should describe that
# tenant's stack, not call itself a template. Applied last -- see the docstring.
RENAMES = (("README.tenant.md", "README.md"),)

# Every file carrying a placeholder. Named by their **pre-rename** paths, so
# that substitution can run before the rename. Adding a placeholder to a file
# that is not in this list is the defect this script and its test exist to make
# impossible.
SUBSTITUTED_FILES = (
    "Pulumi.yaml",
    ".github/workflows/infra-ci.yml",
    "README.tenant.md",
)

# Directories the final sweep does not walk. `.git` and `node_modules` are not
# authored content; `scripts` holds the pattern itself as data.
SWEEP_EXCLUDED_DIRS = ("scripts", ".git", "node_modules")

# A file the sweep cannot decode cannot be shown to hold no placeholder, so it is
# a finding. Only these lowercase suffixes excuse a file, and only once decoding
# has failed: a text file with one of these suffixes is still swept, and an
# OSError is never excused.
SWEEP_BINARY_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".pdf",
    ".zip",
    ".gz",
)

# A `#` comment line naming a placeholder is documenting it, not carrying one.
# **Not applied to markdown**, where `#` opens a heading: `# __TENANT_NAME__` is
# the single most likely place for a real surviving placeholder, and treating it
# as a comment would hide exactly that.
COMMENT_LINE = re.compile(r"^\s*#")
COMMENT_EXEMPT_SUFFIXES = (".md",)

# Separates a file path from the reason it could not be read. A placeholder
# finding is `path:line: TOKEN`, so an entry holding this marker is unreadable.
UNREADABLE_MARKER = ": cannot read: "


class GenerateError(Exception):
    """Raised for anything a caller could have avoided, or that the tree refused."""


def taken_names(register: pathlib.Path | None = None) -> dict[str, str]:
    """The register's names, each mapped to its kind.

    Refuses a missing, unreadable or malformed register rather than reading it
    as empty: an empty list would accept every name, which is the failure this
    exists to prevent.
    """
    path = REGISTER_FILE if register is None else register
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GenerateError(
            f"the tenant name register copy {path.name} cannot be read ({type(exc).__name__}); "
            "refusing every name rather than none"
        ) from exc
    entries = document.get("names") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        raise GenerateError(
            f"the tenant name register copy {path.name} has no `names` list; refusing every name "
            "rather than none"
        )
    names: dict[str, str] = {}
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else None
        kind = entry.get("kind") if isinstance(entry, dict) else None
        if not isinstance(name, str) or kind not in REGISTER_KINDS:
            raise GenerateError(
                f"the tenant name register copy {path.name} holds an entry that is not a name "
                f"with a kind of {' or '.join(REGISTER_KINDS)}: {entry!r}"
            )
        names[name] = kind
    return names


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
    taken = taken_names()
    if slug in taken:
        raise GenerateError(
            f"slug {slug!r} is already registered as a {taken[slug]} name -- a host or tenant "
            "already uses it, and taking it would overwrite that stack's directory, secrets file "
            "and systemd unit. See the tenant name register in branchLeft/ghost-platform."
        )
    return slug


def substitutions(slug: str) -> dict[str, str]:
    return {
        # The Pulumi project name. Every tenant stack shares one state bucket
        # and the object path derives from this, so it must be unique.
        "__TENANT_PULUMI_PROJECT__": f"{slug}-infra",
        "__TENANT_NAME__": slug,
    }


def remove_template_only(root: pathlib.Path) -> list[str]:
    """Delete what belongs to the template. Idempotent: a missing path is fine."""
    actions = []
    for name in TEMPLATE_ONLY_PATHS:
        path = root / name
        if path.is_dir():
            shutil.rmtree(path)
            actions.append(f"removed {name}/")
        elif path.exists():
            path.unlink()
            actions.append(f"removed {name}")
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


def sweep(root: pathlib.Path) -> list[str]:
    """Return `path:line: TOKEN` for each placeholder still in the tree, and
    `path: cannot read: ExceptionType` for each file that could not be read."""
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] in SWEEP_EXCLUDED_DIRS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            if path.suffix.lower() not in SWEEP_BINARY_SUFFIXES:
                findings.append(f"{relative}{UNREADABLE_MARKER}{type(error).__name__}")
            continue
        except OSError as error:
            findings.append(f"{relative}{UNREADABLE_MARKER}{type(error).__name__}")
            continue
        skip_comments = path.suffix not in COMMENT_EXEMPT_SUFFIXES
        for number, line in enumerate(text.splitlines(), start=1):
            if skip_comments and COMMENT_LINE.match(line):
                continue
            found = PLACEHOLDER.findall(line)
            if found:
                findings.append(f"{relative}:{number}: {', '.join(sorted(set(found)))}")
    return findings


def generate(root: pathlib.Path, slug: str) -> list[str]:
    validate_slug(slug)
    actions = remove_template_only(root)
    actions.extend(apply_substitutions(root, slug))
    actions.extend(apply_renames(root))
    findings = sweep(root)
    unreadable = [finding for finding in findings if UNREADABLE_MARKER in finding]
    placeholders = [finding for finding in findings if UNREADABLE_MARKER not in finding]
    blocks = []
    if placeholders:
        blocks.append(
            "placeholders survived generation in files SUBSTITUTED_FILES does not name:\n  "
            + "\n  ".join(placeholders)
            + "\nAdd each file to SUBSTITUTED_FILES in this script and to README.md's "
            "placeholder table."
        )
    if unreadable:
        blocks.append(
            "the sweep could not read these files, so it cannot show they hold no placeholder:\n  "
            + "\n  ".join(unreadable)
            + "\nMake each file readable UTF-8 text, or, if it is a binary, give it a suffix on "
            "SWEEP_BINARY_SUFFIXES in this script."
        )
    if blocks:
        raise GenerateError("\n".join(blocks))
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
