"""No tenant-repo document describes the retired Cloud Run model.

A generated tenant repo carries these documents, so a stale line in one reaches
every tenant. The workflows are out of scope: they name the retired model in
their own guards, which refuse it.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RETIRED_MODEL = re.compile(r"Cloud Run|cloud-run|run\.app", re.IGNORECASE)
SKIPPED_DIRS = {".git", "node_modules"}


def documents() -> list[Path]:
    """Every Markdown file on disk, so it also holds in a generated repo, where
    README.tenant.md has become README.md and some scripts are gone."""
    return sorted(
        path
        for path in REPO_ROOT.rglob("*.md")
        if not SKIPPED_DIRS.intersection(path.relative_to(REPO_ROOT).parts)
    )


def retired_model_lines(relpath: str, text: str) -> list[str]:
    """Each line of `text` that names the retired model, as `path:line: text`."""
    return [
        f"{relpath}:{number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), start=1)
        if RETIRED_MODEL.search(line)
    ]


class NoCloudRunTextTests(unittest.TestCase):
    def test_no_document_names_the_retired_model(self) -> None:
        found: list[str] = []
        for path in documents():
            found += retired_model_lines(
                str(path.relative_to(REPO_ROOT)), path.read_text(encoding="utf-8")
            )
        self.assertEqual(found, [])

    def test_the_scan_sees_a_named_line(self) -> None:
        sample = "fine\npassed a reference to Cloud Run\n"
        self.assertEqual(
            retired_model_lines("x.md", sample), ["x.md:2: passed a reference to Cloud Run"]
        )


if __name__ == "__main__":
    unittest.main()
