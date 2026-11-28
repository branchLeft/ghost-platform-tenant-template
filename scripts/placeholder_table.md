# placeholder_table

The one table of template placeholders: each placeholder's name, the value it
becomes in a generated tenant repo, and the files that carry it while this repo
is still the template.

- The template generator substitutes from this table.
- The placeholder check reads it, and a test fails when `README.md`'s placeholder
  table disagrees with it.

A placeholder is added to this table and nowhere else.

This module ships in a generated tenant repo: the tenant's own CI runs the
placeholder check, which imports it.
