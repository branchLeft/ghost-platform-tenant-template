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
