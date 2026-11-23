"""Shared helpers for the unit tests in this directory.

Not a test module: the runner collects `test_*.py` only, so this file is
imported by the tests and never run as one. It is test-only, so the generator
strips it from a tenant repo (`TEMPLATE_ONLY_PATHS`).
"""

import importlib.util
import pathlib
import subprocess
import sys
from types import ModuleType

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO = SCRIPTS.parent


def load_script(filename: str, module_name: str) -> ModuleType:
    """Import a script in this directory by path, under `module_name`.

    Scripts have hyphenated filenames, which a plain import cannot name. The
    module is registered in `sys.modules` so that `patch` targets resolve.
    """
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def tracked_files() -> list[str]:
    """Every path git tracks in this repo, relative to its root."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
    )
    return [name for name in listing.stdout.decode().split("\0") if name]
