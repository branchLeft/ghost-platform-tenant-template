# assert-placeholders-substituted.py

Guards both directions of template placeholder substitution.

Every value in this repo that must be unique per generated tenant repo is
committed as a `__SOME_NAME__` placeholder (see README.md's placeholder
table), meant to be substituted by the provisioning flow when the repo is
generated from this template. Three checks live here because the correct
state of a placeholder is a _different_ fact in each place this repo's CI
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
`--stack` flag errors with "stack not found". The Pulumi _project_ name in
Pulumi.yaml is not: nothing sends it to an API, and Pulumi's own project-name
grammar (alphanumerics, hyphens, underscores, periods) accepts the placeholder
text unchanged, so an unsubstituted one produces a valid-looking state object
path under a name no human chose. Every tenant stack now shares one state
bucket, so that is worse than it was when each had its own: two tenants
generated without substitution would collide on the same object path.

## Usage

    assert-placeholders-substituted.py [file ...]
    assert-placeholders-substituted.py --mangled [file ...]
    assert-placeholders-substituted.py --template

Exit status 0 if the relevant check passes, 1 if it does not.

## Tests (`test_assert_placeholders_substituted.py`)

The interesting test is not that the script finds a placeholder in a file it
was handed -- it is `test_default_files_covers_every_placeholder_in_the_tree`,
which is the only thing standing between a future edit and a placeholder that
travels into a generated tenant repo unchecked. `DEFAULT_FILES` is a hand-kept
list, and a file dropped from it stops being covered silently.
