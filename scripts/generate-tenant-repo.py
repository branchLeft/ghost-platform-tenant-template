#!/usr/bin/env python3
"""Turn a fresh clone of a template-generated repo into one tenant's repo.

Run by the platform's provisioning flow, from the root of the clone, once:

    generate-tenant-repo.py --slug blog

It removes what belongs to the template and not to a tenant, substitutes every
`__LIKE_THIS__` placeholder, renames the tenant-facing README over the
template-facing one, and then refuses if any placeholder survives anywhere.

**It lives here rather than in the provisioning workflow on purpose.** What a
tenant repo should and should not contain is a property of the template — a
copy of that knowledge in another repository's workflow goes stale the first
time a file is added here. The failure is silent and it propagates.

**Removal is not tidiness; it is what makes a generated repo's CI pass.** The
template's own test suite asserts template-only facts — that `README.tenant.md`
exists, that `Pulumi.yaml` still carries an unsubstituted placeholder — and both
jobs in the generated `infra-ci.yml` run `unittest discover -s scripts`. Left in
place, every tenant repo fails its own type-check job from birth, `deploy` never
runs because it `needs: [typecheck]`, and no tenant ever deploys. The graph
artefact is worse than useless in a tenant: it describes the template, while the
generated `CLAUDE.md` tells agents to answer questions from it.

Order matters, and it is the reverse of the obvious one. The rename runs
**last**: a failure after it would leave `README.tenant.md` gone and a re-run
would die naming the wrong cause. Removals are idempotent, so everything before
the rename is safe to re-run.

Exit 0 on success, 1 on any refusal, 2 on usage error.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys

# Mirrors `validateTenantSlug` in @branchleft/ghost-platform-tenant. The slug
# becomes a Compose project, a systemd instance name, a directory, a MySQL
# identifier and two volume names, so a value valid in one and not another is a
# tenant that provisions and then cannot start. The trailing character is
# restricted to a letter or digit for the same reason it is on the component's
# side: the slug also becomes an S3-compatible media bucket name, and bucket
# naming rules require a bucket name to both start and end with one.
# `branchLeft/workspace#681` found this copy had drifted to accept a trailing
# hyphen; `test_charset_matches_the_installed_component_across_a_battery`
# below now runs a full battery of boundary slugs through the installed
# component, rather than the length and reserved-name constants that
# `test_bounds_match_the_installed_component` alone compared, so a future
# drift in the charset itself is caught the same way.
SLUG = re.compile(r"\A[a-z]([a-z0-9-]*[a-z0-9])?\Z")

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

# Removed from the generated repo. Each one either asserts something only true
# of the template, or describes the template rather than the tenant.
#
# `graphify-out/` is ~400 KB of graph describing this repository, and the
# workspace convention already names a generated single-stack tenant repo as a
# deliberate graphify exception -- so its workflow goes too, rather than
# rebuilding that graph on a schedule for every tenant forever at API cost.
TEMPLATE_ONLY_PATHS = (
    "scripts/test_generate_tenant_repo.py",
    "scripts/test_assert_placeholders_substituted.py",
    ".github/workflows/graphify.yml",
    "graphify-out",
    # Last, and self-referential: this script is already loaded, so unlinking it
    # mid-run is safe on POSIX, but it is ordered last so that a failure leaves
    # the generator present for a re-run.
    "scripts/generate-tenant-repo.py",
)

# Everything between these markers is dropped from the file that carries them.
# Used for the graphify guidance in `CLAUDE.md`, which would otherwise point a
# tenant repo's agents at a graph this script has just deleted.
BLOCK_START = "<!-- template-only:start -->"
BLOCK_END = "<!-- template-only:end -->"
BLOCK_FILES = ("CLAUDE.md",)

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

# A `#` comment line naming a placeholder is documenting it, not carrying one.
# **Not applied to markdown**, where `#` opens a heading: `# __TENANT_NAME__` is
# the single most likely place for a real surviving placeholder, and treating it
# as a comment would hide exactly that.
COMMENT_LINE = re.compile(r"^\s*#")
COMMENT_EXEMPT_SUFFIXES = (".md",)


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


def strip_template_only_blocks(root: pathlib.Path) -> list[str]:
    actions = []
    for name in BLOCK_FILES:
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if BLOCK_START not in text:
            continue
        if text.count(BLOCK_START) != text.count(BLOCK_END):
            raise GenerateError(
                f"{name} has {text.count(BLOCK_START)} template-only start markers and "
                f"{text.count(BLOCK_END)} end markers. Refusing to guess where a block ends."
            )
        stripped = re.sub(
            re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?",
            "",
            text,
            flags=re.DOTALL,
        )
        path.write_text(stripped, encoding="utf-8")
        actions.append(f"stripped template-only block(s) from {name}")
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
    """Return every `path:line` still carrying a placeholder token."""
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
    actions.extend(strip_template_only_blocks(root))
    actions.extend(apply_substitutions(root, slug))
    actions.extend(apply_renames(root))
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
