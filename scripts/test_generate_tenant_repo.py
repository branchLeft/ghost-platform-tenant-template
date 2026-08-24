"""Unit tests for generate-tenant-repo.py.

The load-bearing one is `test_generating_this_repos_own_tree_leaves_no_token`:
it runs the real generation against a copy of this repo's actual tracked files,
so a placeholder added to a file nobody remembered to list fails here rather
than shipping into a tenant repo. That is the whole reason the substitution set
lives in this repository instead of in the provisioning workflow.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO = SCRIPTS.parent

_spec = importlib.util.spec_from_file_location(
    "generate_tenant_repo", SCRIPTS / "generate-tenant-repo.py"
)
assert _spec and _spec.loader
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def tracked_files() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, check=True, text=True
    )
    return [name for name in listing.stdout.splitlines() if name]


def component_constants() -> dict | None:
    """The component's own slug bounds, or None when it is not installed."""
    if not (REPO / "node_modules" / "@branchleft" / "ghost-platform-tenant").is_dir():
        return None
    script = (
        "const c = require('@branchleft/ghost-platform-tenant');"
        "console.log(JSON.stringify({"
        "reserved: c.RESERVED_STACK_NAMES,"
        "maxSlugLength: c.MAX_TENANT_SLUG_LENGTH}))"
    )
    try:
        result = subprocess.run(
            ["node", "-e", script], cwd=REPO, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return json.loads(result.stdout)


def copy_repo(destination: pathlib.Path) -> pathlib.Path:
    for name in tracked_files():
        if name.startswith("graphify-out/"):
            continue
        source = REPO / name
        if not source.is_file():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # `git ls-files` output is filtered above to keep the copy cheap, so the
    # graph artefact is re-created as a stub. Without it the "graphify-out is
    # removed" assertion would pass against a tree that never had one.
    graph = destination / "graphify-out"
    graph.mkdir(exist_ok=True)
    (graph / "graph.json").write_text('{"stub": true}\n', encoding="utf-8")
    return destination


class ValidateSlug(unittest.TestCase):
    def test_accepts_an_ordinary_slug(self) -> None:
        self.assertEqual(module.validate_slug("acme-blog"), "acme-blog")

    def test_refuses_uppercase_underscores_and_a_leading_digit(self) -> None:
        for bad in ("Acme", "acme_blog", "1acme", "-acme", "acme "):
            with self.subTest(bad=bad):
                with self.assertRaises(module.GenerateError):
                    module.validate_slug(bad)

    def test_refuses_a_slug_past_the_mysql_identifier_bound(self) -> None:
        with self.assertRaises(module.GenerateError):
            module.validate_slug("a" * (module.MAX_SLUG_LENGTH + 1))

    def test_refuses_every_reserved_stack_name(self) -> None:
        # A tenant taking one of these overwrites a platform stack's directory,
        # secrets file and systemd unit on the app host.
        for reserved in module.RESERVED_SLUGS:
            with self.subTest(reserved=reserved):
                with self.assertRaises(module.GenerateError):
                    module.validate_slug(reserved)

    def test_bounds_match_the_installed_component(self) -> None:
        # The component is the authority; this script's copies exist so
        # generation can refuse before a repository is created. A copy that
        # drifts fails open -- a repo gets created for a slug whose stack then
        # refuses at preview, which is the expensive order to find out in.
        #
        # Read by executing the package rather than by parsing its compiled
        # output: a regex over `dist/*.js` matches the `void 0` initialiser
        # TypeScript emits ahead of the real assignment, which is how an earlier
        # draft of this test passed against the wrong value.
        constants = component_constants()
        if constants is None:
            self.skipTest("component not installed or node unavailable; run npm ci")
        self.assertEqual(
            sorted(constants["reserved"]),
            sorted(module.RESERVED_SLUGS),
            "RESERVED_SLUGS has drifted from the component's RESERVED_STACK_NAMES",
        )
        self.assertEqual(
            constants["maxSlugLength"],
            module.MAX_SLUG_LENGTH,
            "MAX_SLUG_LENGTH has drifted from the component's MAX_TENANT_SLUG_LENGTH",
        )


class Substitutions(unittest.TestCase):
    def test_project_name_is_the_slug_plus_infra(self) -> None:
        self.assertEqual(
            module.substitutions("blog")["__TENANT_PULUMI_PROJECT__"], "blog-infra"
        )

    def test_tenant_name_is_the_bare_slug(self) -> None:
        self.assertEqual(module.substitutions("blog")["__TENANT_NAME__"], "blog")


class Generate(unittest.TestCase):
    def test_generating_this_repos_own_tree_leaves_no_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            actions = module.generate(root, "acme-blog")
            self.assertIn("no placeholder survives anywhere in the tree", actions)

    def test_the_tenant_readme_replaces_the_template_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            module.generate(root, "acme-blog")
            self.assertFalse((root / "README.tenant.md").exists())
            readme = (root / "README.md").read_text(encoding="utf-8")
            self.assertTrue(readme.startswith("# acme-blog "))

    def test_the_generated_repo_passes_its_own_ci(self) -> None:
        """The finding this test exists for: every tenant repo was red from birth.

        The template's own suite asserts template-only facts -- that
        `README.tenant.md` exists, that `Pulumi.yaml` still carries an
        unsubstituted placeholder -- and both jobs in the generated
        `infra-ci.yml` run `unittest discover -s scripts`. Left in place they
        fail in every generated repo, `deploy` never runs because it
        `needs: [typecheck]`, and no tenant ever deploys. Asserting on files is
        what missed it; running what the generated repo's CI runs is what
        catches it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            module.generate(root, "acme-blog")
            for command in (
                ["python3", "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"],
                ["python3", "scripts/assert-placeholders-substituted.py"],
                ["python3", "scripts/assert-no-committed-pulumi-secrets.py", "--self-test"],
                ["python3", "scripts/assert-no-committed-pulumi-secrets.py", "--scan-tree", "."],
            ):
                result = subprocess.run(command, cwd=root, capture_output=True, text=True)
                self.assertEqual(
                    result.returncode,
                    0,
                    f"a generated tenant repo fails `{' '.join(command)}`:\n"
                    f"{result.stdout}\n{result.stderr}",
                )

    def test_template_only_artefacts_do_not_reach_a_tenant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            module.generate(root, "acme-blog")
            for name in module.TEMPLATE_ONLY_PATHS:
                self.assertFalse((root / name).exists(), f"{name} survived generation")

    def test_the_generated_claude_md_does_not_point_at_a_deleted_graph(self) -> None:
        # The graph artefact and its workflow are removed, so guidance telling a
        # tenant repo's agents to answer from `graphify-out/` would send them at
        # a directory that is not there -- and, worse, would have described the
        # template rather than the tenant if it were.
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            module.generate(root, "acme-blog")
            claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertNotIn("graphify", claude)
            self.assertNotIn(module.BLOCK_START, claude)
            self.assertNotIn(module.BLOCK_END, claude)

    def test_the_pulumi_project_name_is_substituted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            module.generate(root, "acme-blog")
            self.assertIn(
                "name: acme-blog-infra", (root / "Pulumi.yaml").read_text(encoding="utf-8")
            )

    def test_a_missing_tenant_readme_refuses_rather_than_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            (root / "README.tenant.md").unlink()
            with self.assertRaises(module.GenerateError):
                module.generate(root, "acme-blog")

    def test_a_placeholder_in_an_unlisted_file_fails_generation(self) -> None:
        # The defect this script exists to prevent: a placeholder added to a
        # file the substitution set does not name ships as a literal token.
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            (root / "tsconfig.json").write_text(
                '{"extends": "__TENANT_NAME__"}\n', encoding="utf-8"
            )
            with self.assertRaises(module.GenerateError) as caught:
                module.generate(root, "acme-blog")
            self.assertIn("tsconfig.json", str(caught.exception))

    def test_a_listed_file_that_moved_refuses_rather_than_skipping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            (root / "Pulumi.yaml").unlink()
            with self.assertRaises(module.GenerateError) as caught:
                module.generate(root, "acme-blog")
            self.assertIn("Pulumi.yaml", str(caught.exception))

    def test_a_reserved_slug_is_refused_before_anything_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            with self.assertRaises(module.GenerateError):
                module.generate(root, "website")
            self.assertTrue((root / "README.tenant.md").is_file())

    def test_a_failure_before_the_rename_leaves_a_re_runnable_tree(self) -> None:
        # The rename runs last precisely so this holds. With it first, any later
        # failure left `README.tenant.md` gone, and the re-run died naming the
        # missing README rather than the thing that actually failed.
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            (root / "Pulumi.yaml").unlink()
            with self.assertRaises(module.GenerateError):
                module.generate(root, "acme-blog")
            self.assertTrue((root / "README.tenant.md").is_file())

    def test_a_placeholder_in_a_markdown_heading_is_not_mistaken_for_a_comment(self) -> None:
        # `#` opens a heading in markdown, and `# __TENANT_NAME__` is the single
        # most likely place for a real surviving placeholder -- the comment
        # exclusion must not reach it.
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            (root / "NOTES.md").write_text("# __TENANT_NAME__\n", encoding="utf-8")
            with self.assertRaises(module.GenerateError) as caught:
                module.generate(root, "acme-blog")
            self.assertIn("NOTES.md", str(caught.exception))


class Main(unittest.TestCase):
    def test_exit_zero_on_a_clean_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            self.assertEqual(module.main(["--slug", "acme-blog", "--root", str(root)]), 0)

    def test_exit_one_on_a_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_repo(pathlib.Path(tmp))
            self.assertEqual(module.main(["--slug", "website", "--root", str(root)]), 1)


if __name__ == "__main__":
    unittest.main()
