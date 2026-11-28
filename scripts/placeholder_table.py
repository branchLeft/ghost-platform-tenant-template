"""The one table of template placeholders; add a placeholder here and nowhere else.

See [placeholder_table.md](placeholder_table.md) for its consumers.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

# Matches any placeholder token, known to this table or not. Shared by the
# generator's sweep and the check, so the two cannot disagree on what a
# placeholder looks like.
PLACEHOLDER_PATTERN: Final = re.compile(r"__[A-Z][A-Z0-9_]*__")

# Present only in the template: generation renames it over README.md. It is
# still substituted before the rename, but a generated repo's own checks do not
# read it, so it is left out of DEFAULT_FILES below.
TEMPLATE_ONLY_FILES: Final[tuple[str, ...]] = ("README.tenant.md",)


@dataclass(frozen=True)
class Placeholder:
    # The bare name, without the surrounding underscores.
    name: str
    # Pre-rename paths, the files that must carry this placeholder while the
    # repo is the template.
    files: tuple[str, ...]
    # The slug -> the value this placeholder becomes in a tenant repo.
    value: Callable[[str], str]

    @property
    def token(self) -> str:
        return f"__{self.name}__"


PLACEHOLDERS: Final[tuple[Placeholder, ...]] = (
    # The Pulumi project name. Every tenant stack shares one state bucket and the
    # object path derives from this, so it must be unique.
    Placeholder(
        name="TENANT_PULUMI_PROJECT",
        files=("Pulumi.yaml",),
        value=lambda slug: f"{slug}-infra",
    ),
    # The Pulumi stack name, equal to the `slug` stack config value.
    Placeholder(
        name="TENANT_NAME",
        files=(".github/workflows/infra-ci.yml", "README.tenant.md"),
        value=lambda slug: slug,
    ),
)


def _substituted_files() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for placeholder in PLACEHOLDERS:
        for name in placeholder.files:
            seen.setdefault(name, None)
    return tuple(seen)


# Every file carrying a placeholder, by pre-rename path, in first-seen order.
SUBSTITUTED_FILES: Final[tuple[str, ...]] = _substituted_files()

# Each placeholder's name mapped to the file(s) it must appear in, intact, while
# this repo is still the template.
TEMPLATE_PLACEHOLDER_FILES: Final[dict[str, list[str]]] = {
    placeholder.name: list(placeholder.files) for placeholder in PLACEHOLDERS
}

# The files a generated repo's own check reads by default: every substituted
# file that is not template-only.
DEFAULT_FILES: Final[list[str]] = [
    name for name in SUBSTITUTED_FILES if name not in TEMPLATE_ONLY_FILES
]


def substitutions(slug: str) -> dict[str, str]:
    """Each placeholder token mapped to the value it becomes for `slug`."""
    return {placeholder.token: placeholder.value(slug) for placeholder in PLACEHOLDERS}
