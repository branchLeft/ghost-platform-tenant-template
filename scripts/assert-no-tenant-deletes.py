#!/usr/bin/env python3
"""Fail if a `pulumi preview --json` plan would destroy protected infrastructure.

The plan-matching logic (`destructive_steps`) mirrors
`branchLeft/ghost-platform`'s `infra/platform/scripts/assert-no-platform-deletes.py`
(same Pulumi CLI, same `--json` output shape). What differs is *which*
resources matter: `PROTECTED`, the coverage check, and the fixtures.

Run as a preflight inside the CI deploy job, against the same stack state
`pulumi up` is about to act on, a few seconds earlier. A pass is therefore a
real statement about that run -- but see "What this cannot prove" below.

Usage:
    assert-no-tenant-deletes.py <preview-json-file>
    assert-no-tenant-deletes.py --self-test
    assert-no-tenant-deletes.py --verify-coverage <package-dist-dir>

Exit status 0 if clean, 1 if a protected resource would be destroyed (or if
any input could not be read or understood), 2 on usage error.

Why this can't just reuse `PROTECTED` from the sibling script: this
program's resources aren't declared with fixed, literal Pulumi logical names
the way the platform stack's are. `GhostTenant` (`@branchleft/ghost-platform-tenant`)
builds each child resource's name as `${tenantName}-<suffix>` from stack
config, so what a real preview reports exists as a literal string nowhere in
source -- only synthesised at runtime. See `_declaration_pattern_tenant_suffix`
below for how coverage-verification adapts to that; `PROTECTED` itself still
needs full names to match real URNs, built from `_TENANT_NAME` (see below).

**Onboarding a second tenant using this same stack name must update this
file** if the tenant name ever changes post-provisioning -- `PROTECTED`'s
tenant-derived entries are literal, not computed at run time, the same
"add it, review it, merge it -- never let CI infer it" pattern this
programme uses for IAM roles.

What this cannot prove -- same two limits as the platform script:

1. `pulumi preview` compares the program to Pulumi *state*, never to live
   GCP. A resource already deleted out-of-band still shows as unchanged
   here. This answers "will this apply destroy something", not "is the
   tenant's infrastructure intact".

2. A rename carrying Pulumi's `aliases` resource option produces an in-place
   update with no destructive step at all, so the plan check reports clean
   while silently retiring the old logical name. `--verify-coverage` is what
   closes that.

Coverage-verification reads the *installed package*, not this repo's source.
Every name in `PROTECTED` is a `GhostTenant` child, and this program declares
no protected resource of its own -- so the question the check has to answer is
"does the pinned `@branchleft/ghost-platform-tenant` still name these
resources the way `PROTECTED` assumes", which is exactly what a version bump
changes.
"""

import json
import pathlib
import re
import sys

# Mirrors the `tenantName` config value this stack is applied with (see
# index.ts and RUNBOOK-bootstrap.md) -- substituted by the provisioning
# script per generated repo, kept in sync with the real applied value by
# hand, same as the rest of `PROTECTED`.
_TENANT_NAME = "__TENANT_NAME__"

PROTECTED = {
    # GhostTenant's own service account. Every other tenant resource either
    # runs as this identity or grants it a narrow permission; losing it
    # breaks the Cloud Run service's access to its own database, secrets and
    # storage prefix, and a recreated SA gets a new unique ID that every
    # existing IAM binding referencing the old one silently stops matching.
    f"{_TENANT_NAME}-sa",
    # gcp.sql.Database, gcp.sql.User. `deletionPolicy: 'ABANDON'` on the
    # Database already makes Pulumi's own delete step non-destructive to
    # live data; guarded anyway because losing *Pulumi's own tracking* of
    # these means the next `pulumi up` generates a new random password with
    # no path to reconcile it against the still-live account.
    f"{_TENANT_NAME}-db",
    f"{_TENANT_NAME}-db-user",
    # gcp.cloudrunv2.Service. Already has `deletionProtection: true` set
    # directly on the resource; guarded here too so this preflight fails
    # fast and clearly, before ever reaching GCP's own protection error.
    f"{_TENANT_NAME}-service",
    # gcp.storage.HmacKey. No native deletion-protection field exists for an
    # HMAC key, and losing it breaks every media upload until a human
    # notices and reconciles a new one.
    f"{_TENANT_NAME}-media-hmac",
    #
    # Deliberately absent, with reasoning:
    #
    # - This repo's CI identity. It is no longer declared here at all: the
    #   deployer service account, the Workload Identity pool and provider and
    #   the impersonation binding are created by the platform's provisioning
    #   flow and live in its state, so no plan produced in this repo can
    #   destroy them. The guard against destroying them belongs to whatever
    #   applies them, and duplicating the names here would guard nothing while
    #   reading as though it did.
    #
    # - The Secret Manager entries (db username, db password, HMAC access
    #   key ID, HMAC secret access key). Each is cheap to regenerate: the
    #   credential it stores already lives in Pulumi state independent of
    #   the Secret Manager resource, so losing the secret and re-applying
    #   recreates it with no data loss -- unlike the SA, DB user, or HMAC
    #   key itself, none of which self-heal.
    # - The conditional IAMMember bindings (cloudsql-client, media-create,
    #   media-read). CI holds no permission to change any of these anyway
    #   (serviceAccounts.ts grants no setIamPolicy and no bucket-scoped
    #   storage role), so guarding them here would add a second, more
    #   confusing failure on top of a 403 that already stops the run.
    # - The DB password (a random.RandomPassword). Generated locally, calls
    #   no GCP API, and a "delete" step against it is pure Pulumi
    #   bookkeeping with nothing live to lose.
}


def _declaration_pattern_tenant_suffix(suffix: str) -> re.Pattern[str]:
    """Match `new <Ctor>(\\`${...tenantName}<suffix>\\`` in the package source.

    GhostTenant's child resources are named from a template literal built
    from a `tenantName` variable, not a plain string -- sometimes bare,
    sometimes accessed off an args object. `[\\w.]*tenantName` tolerates
    both without needing to track which file uses which spelling.

    Checked against the *installed package's* compiled output
    (`node_modules/@branchleft/ghost-platform-tenant/dist/*.js`), not this
    repo's own source -- the component lives there, pinned to a version, so
    this check is really asking "does the pinned version still name this
    resource the way PROTECTED assumes", which is exactly what changes on a
    version bump. TypeScript's compiler preserves template-literal source
    text verbatim, so the same pattern matches the compiled `.js` as would
    have matched the original `.ts`.
    """
    return re.compile(rf"""\bnew\s+[\w.]+\(\s*`\$\{{[\w.]*tenantName\}}{re.escape(suffix)}`""")


# Any Pulumi step op whose name contains either of these destroys, or
# schedules the destruction of, the resource it names -- covers delete,
# delete-replaced, replace, create-replacement, etc. Substring matching on
# the op name is the fail-closed choice.
DESTRUCTIVE_SUBSTRINGS = ("delete", "replace")


def destructive_steps(plan: dict) -> list[tuple[str, str]]:
    """Return (name, op) for every protected resource this plan would destroy.

    Raises ValueError if `plan` is not a preview plan, rather than returning
    an empty list -- "I could not find any steps" and "there are no
    destructive steps" must not produce the same exit code.
    """
    steps = plan.get("steps")
    if not isinstance(steps, list):
        raise ValueError("no 'steps' array in the preview JSON")

    found: list[tuple[str, str]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError(f"malformed step entry: {step!r}")
        op = step.get("op")
        urn = step.get("urn")
        if not isinstance(op, str) or not isinstance(urn, str):
            raise ValueError(f"step missing op/urn: {step!r}")
        if not any(word in op for word in DESTRUCTIVE_SUBSTRINGS):
            continue
        name = urn.rsplit("::", 1)[-1]
        if name in PROTECTED:
            found.append((name, op))
    return found


# Fixture URNs below use a shape matching a real GhostTenant child resource --
# component children carry the parent's own type in the URN chain, which
# `destructive_steps` doesn't need to parse since it only looks at the text
# after the last `::`. Op values for the destructive cases are synthesized:
# no real destroy plan exists to copy from a stack this template has never
# applied.
_URN_SA = (
    f"urn:pulumi:{_TENANT_NAME}::ghost-platform-tenant::"
    f"ghostPlatform:tenant:GhostTenant$gcp:serviceaccount/account:Account::{_TENANT_NAME}-sa"
)
_URN_DB = (
    f"urn:pulumi:{_TENANT_NAME}::ghost-platform-tenant::"
    f"ghostPlatform:tenant:GhostTenant$gcp:sql/database:Database::{_TENANT_NAME}-db"
)
_URN_DB_USER = (
    f"urn:pulumi:{_TENANT_NAME}::ghost-platform-tenant::"
    f"ghostPlatform:tenant:GhostTenant$gcp:sql/user:User::{_TENANT_NAME}-db-user"
)
_URN_UNPROTECTED = (
    f"urn:pulumi:{_TENANT_NAME}::ghost-platform-tenant::"
    "gcp:projects/service:Service::api-storage.googleapis.com"
)


def _plan(*steps: tuple[str, str]) -> dict:
    return {"steps": [{"op": op, "urn": urn} for op, urn in steps]}


def self_test() -> int:
    cases: list[tuple[str, dict, list[tuple[str, str]]]] = [
        ("clean create-only plan", _plan(("create", _URN_SA)), []),
        ("no-op plan", _plan(("same", _URN_SA), ("same", _URN_DB)), []),
        ("update is not a destroy", _plan(("update", _URN_SA)), []),
        ("outright delete", _plan(("delete", _URN_SA)), [(f"{_TENANT_NAME}-sa", "delete")]),
        (
            "replacement counts",
            _plan(("replace", _URN_DB)),
            [(f"{_TENANT_NAME}-db", "replace")],
        ),
        (
            "the create half of a replacement still counts",
            _plan(("create-replacement", _URN_DB_USER)),
            [(f"{_TENANT_NAME}-db-user", "create-replacement")],
        ),
        (
            "delete-replaced counts",
            _plan(("delete-replaced", _URN_DB_USER)),
            [(f"{_TENANT_NAME}-db-user", "delete-replaced")],
        ),
        ("unprotected resource deleted", _plan(("delete", _URN_UNPROTECTED)), []),
        (
            "one protected delete hidden among unprotected churn",
            _plan(
                ("create", _URN_UNPROTECTED),
                ("delete", _URN_UNPROTECTED),
                ("update", _URN_DB_USER),
                ("delete", _URN_DB),
            ),
            [(f"{_TENANT_NAME}-db", "delete")],
        ),
        ("empty plan", _plan(), []),
    ]

    failed = False
    for name, plan, expected in cases:
        actual = destructive_steps(plan)
        ok = actual == expected
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}: {name} -> {actual!r} (expected {expected!r})")

    for name, bad in [
        ("plan with no steps key", {}),
        ("steps is not a list", {"steps": "nope"}),
        ("step missing urn", {"steps": [{"op": "delete"}]}),
    ]:
        try:
            destructive_steps(bad)
        except ValueError:
            print(f"PASS: {name} raises rather than reporting clean")
        else:
            failed = True
            print(f"FAIL: {name} returned clean instead of raising")

    failed |= _coverage_self_test() != 0

    if failed:
        print("\nThis gate no longer behaves as written. It would report success")
        print("against a plan that destroys a protected tenant resource.")
    return 1 if failed else 0


# The five tenant-derived suffixes, checked against the package's compiled
# output rather than a literal name -- see `_declaration_pattern_tenant_suffix`.
_TENANT_SUFFIXES = {"-sa", "-db", "-db-user", "-service", "-media-hmac"}


def _coverage_self_test() -> int:
    """Prove `--verify-coverage` still fails when the pinned package drifts."""
    import tempfile

    # Compiled-JS shape: a plain `var`/`const`, not TypeScript `export const`.
    suffix_decls = "\n".join(
        f"const t{i} = new gcp.some.Type(\n  `${{tenantName}}{suffix}`,\n  {{}}\n);"
        for i, suffix in enumerate(sorted(_TENANT_SUFFIXES))
    )
    suffix_renamed = suffix_decls.replace("${tenantName}-service", "${tenantName}-cloudrun", 1)
    # A rename that leaves the old text present as a comment and a plain
    # string constant -- the shape that made a substring check report a false
    # pass.
    suffix_renamed += (
        "\n// was ${tenantName}-service before the rename\n"
        "const legacy = `${tenantName}-service`;\n"
    )

    cases = [
        ("every protected suffix declared", suffix_decls, 0),
        ("one renamed, old name still mentioned in a comment/constant", suffix_renamed, 1),
    ]

    failed = False
    with tempfile.TemporaryDirectory() as root:
        dist_dir = pathlib.Path(root) / "dist"
        dist_dir.mkdir()
        for name, dist_source, expected in cases:
            (dist_dir / "component.js").write_text(dist_source, encoding="utf-8")
            actual = verify_coverage(str(dist_dir))
            ok = actual == expected
            failed |= not ok
            print(f"{'PASS' if ok else 'FAIL'}: coverage, {name} -> exit {actual} (expected {expected})")

        empty = pathlib.Path(root) / "empty"
        empty.mkdir()
        actual = verify_coverage(str(empty))
        ok = actual == 1
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}: coverage, no package files -> exit {actual} (expected 1)")

        actual = verify_coverage(str(pathlib.Path(root) / "does-not-exist"))
        ok = actual == 1
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}: coverage, missing directory -> exit {actual} (expected 1)")

    return 1 if failed else 0


def verify_coverage(package_dist_dir: str) -> int:
    """Fail if a PROTECTED suffix no longer appears in the installed package.

    Checked against the installed package's compiled output
    (`node_modules/@branchleft/ghost-platform-tenant/dist/*.js`), which ships
    no `.ts` sources (its `package.json` `files` field excludes them). Every
    name in `PROTECTED` is a `GhostTenant` child; this repo's own program
    declares no protected resource, so there is nothing here to check against.
    """
    dist_dir = pathlib.Path(package_dist_dir)
    if not dist_dir.is_dir():
        print(f"::error::not a directory: {package_dist_dir}")
        return 1

    dist_sources = sorted(dist_dir.glob("*.js"))
    if not dist_sources:
        print(f"::error::no .js files found in {package_dist_dir}; nothing was checked")
        return 1

    dist_blob = "\n".join(path.read_text(encoding="utf-8") for path in dist_sources)
    missing = [
        suffix
        for suffix in sorted(_TENANT_SUFFIXES)
        if not _declaration_pattern_tenant_suffix(suffix).search(dist_blob)
    ]

    if missing:
        print(
            "::error::these tenant-resource suffixes are not declared (as "
            "`${...tenantName}<suffix>`) by any resource in "
            f"{package_dist_dir}/*.js: {', '.join(missing)}. The "
            "@branchleft/ghost-platform-tenant package may have renamed a "
            "resource at the pinned version -- check its changelog and "
            "update PROTECTED/_TENANT_SUFFIXES to match before trusting "
            "this gate again."
        )
        return 1

    print(
        f"OK: all {len(_TENANT_SUFFIXES)} tenant-derived protected resources "
        f"still appear in {len(dist_sources)} package file(s)."
    )
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) == 3 and argv[1] == "--verify-coverage":
        return verify_coverage(argv[2])
    if len(argv) != 2:
        print(__doc__)
        return 2

    try:
        with open(argv[1], encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"::error::cannot read preview JSON: {error}")
        return 1

    try:
        found = destructive_steps(plan)
    except ValueError as error:
        print(f"::error::preview JSON is not a plan this gate understands: {error}")
        return 1

    if found:
        detail = ", ".join(f"{name} ({op})" for name, op in sorted(found))
        print(
            "::error::this apply would DESTROY protected tenant "
            f"infrastructure: {detail}. Nothing has been applied. If this is "
            "genuinely intended it is a platform-owner-gated migration, not a "
            "merge -- see RUNBOOK-bootstrap.md."
        )
        return 1

    steps = plan.get("steps", [])
    summary = plan.get("changeSummary", {})
    print(
        f"OK: no protected resource is destroyed by this plan "
        f"({len(steps)} steps checked, changeSummary={summary})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
