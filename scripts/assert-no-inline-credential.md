# assert-no-inline-credential.py

Refuses a credential presented as a paste-and-run inline assignment.

`export SOME_PASSWORD='<placeholder>'` and `export SOME_PASSWORD='…'` both
read as copy-pasteable instructions, and both fail the same way: pasted
verbatim, the variable is set to the literal placeholder text rather than a
real credential. That failure is silent -- no error, nothing distinguishes it
from a working assignment -- and it surfaces later, somewhere else, as an
unexplained auth failure or (worse) a malformed value appended to a committed
config file.

The fix this guard enforces is not "add a comment" -- it is: never type a
credential after an `=`. Read it into a variable with a prompt that does not
echo (`read -rs`, one line, pasted alone) and export that instead. A runbook
following that shape never has a literal secret, real or placeholder, sitting
in an `export NAME=...` or a `printf ... >>` line in the first place.

Two placeholder shapes both have to be caught, not just the obvious one: the
angle-bracket form (`<the value>`) and the ellipsis form (`'…'`), because a
token-shape guard that only knows about `<...>` misses the second one
entirely -- proven by `ghost-tenant-blog`'s README, which used it.

Scope: markdown files only (RUNBOOK*.md, README*.md, *.md generally) -- this
is a guard on instructions meant to be pasted into a shell, not on committed
config or code, which `assert-no-committed-pulumi-secrets.py` already covers.

## Usage

    assert-no-inline-credential.py [file ...]

Exit status 0 if no file carries the pattern, 1 if any does.

## Tests (`test_assert_no_inline_credential.py`)

The interesting tests are the two placeholder shapes
(`test_ellipsis_form_is_caught`, matching `test_angle_bracket_form_is_caught`)
and the two must-not-flag controls: a non-credential export
(`test_non_credential_export_is_not_flagged`, the "address half" the owner's
principle deliberately leaves alone) and the safe `read`-based replacement
itself (`test_the_safe_read_pattern_is_not_flagged`) -- a guard that flags its
own remedy would train people to route around it.
