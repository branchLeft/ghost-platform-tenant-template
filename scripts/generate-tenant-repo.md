# generate-tenant-repo.py

Turns a fresh clone of a template-generated repo into one tenant's repo.

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
runs because it `needs: [typecheck]`, and no tenant ever deploys.

Order matters, and it is the reverse of the obvious one. The rename runs
**last**: a failure after it would leave `README.tenant.md` gone and a re-run
would die naming the wrong cause. Removals are idempotent, so everything before
the rename is safe to re-run.

Exit 0 on success, 1 on any refusal, 2 on usage error.

## `SLUG`

Mirrors `validateTenantSlug` in `@branchleft/ghost-platform-tenant`. The slug
becomes a Compose project, a systemd instance name, a directory, a MySQL
identifier and two volume names, so a value valid in one and not another is a
tenant that provisions and then cannot start. The trailing character is
restricted to a letter or digit for the same reason it is on the component's
side: the slug also becomes an S3-compatible media bucket name, and bucket
naming rules require a bucket name to both start and end with one.

This copy previously drifted to accept a trailing hyphen, which fails
downstream at bucket-name validation after a repository already exists for it.
`test_charset_matches_the_installed_component_across_a_battery` in
`test_generate_tenant_repo.py` now runs a full battery of boundary slugs
through the installed component, rather than the length and reserved-name
constants that `test_bounds_match_the_installed_component` alone compared, so
a future drift in the charset itself is caught the same way.

## `tenant-names.json`

The names that are already taken -- an app host's own stacks and every existing
tenant -- are not listed in this script. `scripts/tenant-names.json` is a
verbatim copy of `registers/tenant-names.json` in branchLeft/ghost-platform, the
one home for that list, and `validate_slug` refuses any name in it, whatever its
kind, as its first act: nothing is removed, substituted or renamed for a refused
slug. A copy that is missing, unreadable or malformed refuses every name rather
than none, because an empty list would accept all of them. The copy is removed
from a generated repo with the script.

The register is public, so `test_the_register_copy_matches_the_published_register`
reads the published file and fails when the copy lags it. To refresh the copy,
replace it with the register verbatim. In CI an unreachable register is a
failure, since a check that skips there would let the copy lag unnoticed; run
elsewhere it skips. `test_bounds_match_the_installed_component` still checks
that every name the installed component refuses is in the copy, as the
component's list is the register's infrastructure names as of its release.

## The sweep

`sweep()` is the last step, and it is what proves no placeholder survives. It
walks every file outside `scripts`, `.git` and `node_modules`, and a placeholder
it finds anywhere is a refusal naming the file and line.

A file it cannot read is a refusal too. That covers invalid UTF-8 and any
`OSError`, such as a permission error. A placeholder in a file the sweep cannot
read cannot be shown to be absent, so the file is reported as
`path: cannot read: ExceptionType`, and generation fails.

The one exception is `SWEEP_BINARY_SUFFIXES`: `.png`, `.jpg`, `.jpeg`, `.gif`,
`.ico`, `.webp`, `.woff`, `.woff2`, `.ttf`, `.otf`, `.pdf`, `.zip` and `.gz`. A
file with one of these suffixes is excused only when decoding fails. A text file
with a binary-looking suffix is still swept, and an `OSError` is never excused.
Adding a binary file to the template therefore means checking that its suffix
is on that list.

## Tests (`test_generate_tenant_repo.py`)

`component_accepts_battery` executes the installed `@branchleft/ghost-platform-tenant`
package rather than parsing anything out of it, for the same reason
`component_constants` does: a regex over compiled output can match the wrong
assignment. This is the mechanism that spans the boundary a unit test in
either repo alone could not close on its own: it runs the _published
artefact_ this template actually depends on, not this repository's copy of
its source.

`test_refuses_a_trailing_hyphen_and_accepts_the_boundary` is the acceptance
test for the trailing-hyphen regression described under `SLUG` above: this is
the fourth (in fact fifth — see the module's `SLUG` comment) copy of the
charset rule found to accept a trailing hyphen, which fails downstream at
`mediaBucketName()` — after a repository already exists for it. The
single-character and maximum-length acceptances are the control: the fix
narrows what is accepted, it does not also start rejecting slugs that were
always valid.

`test_charset_matches_the_installed_component_across_a_battery`: the constants
comparisons elsewhere cover length and the reserved list; neither proves the
charset itself still agrees — a past regression was exactly that, a charset
drift with an unchanged length and reserved list. Comparing decisions on a
shared battery, rather than any regex text, is what still works if either
side's pattern is rewritten to an equivalent but differently-spelled form.

`test_the_generated_repo_passes_its_own_ci` is the finding this test exists
for: every tenant repo was red from birth. The template's own suite asserts
template-only facts — that `README.tenant.md` exists, that `Pulumi.yaml`
still carries an unsubstituted placeholder — and both jobs in the generated
`infra-ci.yml` run `unittest discover -s scripts`. Left in place they fail in
every generated repo, `deploy` never runs because it `needs: [typecheck]`, and
no tenant ever deploys. Asserting on files is what missed it; running what the
generated repo's CI runs is what catches it.

## Taken names (moved from `generate-tenant-repo.py`, CMT-3)

Every name that is already taken -- an app host's own stacks and each
existing tenant -- is in the tenant name register, `registers/tenant-names.json`
in branchLeft/ghost-platform, the one home for that list. This script keeps no
list of its own: it reads `tenant-names.json` beside it, a verbatim copy of the
register, and `test_generate_tenant_repo.py` fails when the copy lags the
register's published version. A tenant taking a taken name would overwrite
that stack's directory, secrets file and systemd unit (or collide with an
existing tenant's repository), so a registered name is refused whatever its
kind -- tenant zero's included, which is not provisioned through here.

## Removal order and placement (moved from `generate-tenant-repo.py`, CMT-3)

Run once, from the root of the clone: generate-tenant-repo.py --slug blog
Exit 0 on success, 1 on any refusal, 2 on usage error. This script's removal
order is reversed, and it lives here rather than in the provisioning workflow.

## Slug rules (moved from `generate-tenant-repo.py`, CMT-3)

Mirrors `validateTenantSlug` in @branchleft/ghost-platform-tenant: the slug
becomes a Compose project, systemd instance, directory, MySQL identifier and
two volume names, so a value valid in one and not another is a tenant that
provisions and then cannot start. The trailing character must be a letter or
digit because the slug is also an S3-compatible media bucket name, and
bucket naming requires that at both ends.

## Slug length cap (moved from `generate-tenant-repo.py`, CMT-3)

MySQL caps an account name at 32 characters, and a tenant's database and its
dedicated user share one name of `ghost_<slug>` -- so the slug has 26 to work
with. Every other limit it meets is looser. The component is the authority;
`test_generate_tenant_repo.py` asserts this copy still equals its
`MAX_TENANT_SLUG_LENGTH`, because a copy that drifts upward fails open: a repo
would be created for a slug whose stack then refuses at preview.
