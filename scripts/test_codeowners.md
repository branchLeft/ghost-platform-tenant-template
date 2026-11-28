# test_codeowners

Tests for `.github/CODEOWNERS`: which paths the owner's team holds.

The file is read with GitHub's own rules: the last matching pattern wins, a
leading slash anchors, a slash-less pattern matches at any depth, a trailing
slash is a directory tree, and a pattern with no owner releases what an earlier
line took. A fixed table of sample paths says whether each is owned. The table
has at least one path for every class the file keeps and one for every class it
releases, so a change that drops an owner, or hands one away, turns a named
sample red.

`CODEOWNERS_FILE` points the tests at another file, which is how the checks are
shown red against the previous file and against a deliberately broken copy.
`python3 scripts/test_codeowners.py --count` prints how many tracked files the
file owns; it reads `git ls-files`, which the tests themselves never do.

The shared SYNC-1 lines are not re-implemented here: the repository's Standards
check reads the real file for those.
