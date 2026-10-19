#!/usr/bin/env python3
"""Tests for image-changed.py: the failure that matters is a `false` over a new image.

The script is run for real (`python3 -I`, as the plan job runs it) against a
local HTTP server that answers with the recorded-shape JSON in
testdata/image-changed/, so the request it builds, the token it sends and every
way an answer can be wrong are all exercised, and nothing leaves this machine.
See image-changed.md."""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse

SCRIPTS = pathlib.Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "image-changed.py"
DATA = SCRIPTS / "testdata" / "image-changed"

REPOSITORY = "example/tenant"
CONFIG = "Pulumi.blog.yaml"
WORKFLOW = "infra-ci.yml"
TOKEN = "stub-token-never-real"
RUN_ID = "9003"
RUN_NUMBER = "13"
BASELINE_SHA = "1212121212121212121212121212121212121212"
OLDER_SHA = "1010101010101010101010101010101010101010"
RUNS_PATH = f"/repos/{REPOSITORY}/actions/workflows/{WORKFLOW}/runs"
CONTENTS_PATH = f"/repos/{REPOSITORY}/contents/{CONFIG}"

PIN_A = "stack-config-pin-a.yaml"
PIN_B = "stack-config-pin-b.yaml"

# The quiet history: the last run to succeed is run 12, nothing earlier is pending.
SUCCESS = "runs-success-12-10.json"
RECENT = "runs-recent-quiet.json"


def load_module():
    spec = importlib.util.spec_from_file_location("image_changed", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(name: str) -> bytes:
    return (DATA / name).read_bytes()


def ok(name: str) -> tuple[int, bytes]:
    return 200, fixture(name)


class FakeApi:
    """The three reads the script makes, answered from fixtures; logs every request.

    `success` and `recent` are the two workflow-run listings (the one filtered to
    `status=success` and the unfiltered one), `contents` maps a commit sha to the
    answer for the stack config at that commit, and `redirect_contents` answers
    the contents read with a redirect."""

    def __init__(self, success, recent, contents, redirect_contents=False, config_name=CONFIG):
        self.requests: list[dict[str, str]] = []
        api = self
        contents_path = f"/repos/{REPOSITORY}/contents/{config_name}"

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                api.requests.append(
                    {
                        "method": "GET",
                        "path": parsed.path,
                        "query": parsed.query,
                        "authorization": self.headers.get("Authorization", ""),
                        "accept": self.headers.get("Accept", ""),
                    }
                )
                if parsed.path == RUNS_PATH:
                    expected = {"branch": ["main"], "event": ["push"], "per_page": ["30"]}
                    listed = {k: v for k, v in query.items() if k != "status"}
                    if listed != expected or query.get("status", ["success"]) != ["success"]:
                        return self.reply(400, b'{"message":"unexpected query"}')
                    status, body = success if "status" in query else recent
                    return self.reply(status, body)
                if parsed.path == contents_path:
                    if redirect_contents:
                        self.send_response(302)
                        self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                        self.end_headers()
                        return None
                    status, body = contents.get(query.get("ref", [""])[0], (404, b'{"message":"Not Found"}'))
                    return self.reply(status, body)
                return self.reply(404, b'{"message":"Not Found"}')

            def reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Outcome:
    def __init__(self, code, stdout, stderr, output, requests):
        self.code, self.stdout, self.stderr, self.output, self.requests = code, stdout, stderr, output, requests

    @property
    def lines(self) -> list[str]:
        return self.output.splitlines()


class ImageCase(unittest.TestCase):
    def run_script(self, current, api, env=None, config_name=CONFIG, output_is_dir=False) -> Outcome:
        """Run the script as the plan job does, in a directory holding the current stack config."""
        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            if current is not None:
                (work / CONFIG).write_bytes(current if isinstance(current, bytes) else fixture(current))
            out = work / "out"
            if output_is_dir:
                out.mkdir()
            environ = {
                "GITHUB_TOKEN": TOKEN,
                "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_RUN_ID": RUN_ID,
                "GITHUB_RUN_NUMBER": RUN_NUMBER,
                "GITHUB_API_URL": api.url,
            }
            environ.update(env or {})
            environ = {k: v for k, v in environ.items() if v is not None}
            done = subprocess.run(
                [sys.executable, "-I", str(SCRIPT), "--workflow", WORKFLOW, "--config", config_name, "--github-output", str(out)],
                cwd=work,
                capture_output=True,
                text=True,
                env=environ,
                check=False,
            )
            text = out.read_text(encoding="utf-8") if out.is_file() else ""
            return Outcome(done.returncode, done.stdout, done.stderr, text, list(api.requests))

    def serve(self, success=ok(SUCCESS), recent=ok(RECENT), contents=None, **kwargs) -> FakeApi:
        if contents is None:
            contents = {}
        api = FakeApi(success, recent, contents, **kwargs)
        self.addCleanup(api.close)
        return api

    def verdict(self, current, baseline, **kwargs) -> Outcome:
        """The script's verdict for the current config against a baseline config at run 12's commit."""
        contents = {BASELINE_SHA: (200, baseline if isinstance(baseline, bytes) else fixture(baseline))}
        env = kwargs.pop("env", None)
        return self.run_script(current, self.serve(contents=contents, **kwargs), env=env)

    def assertAsks(self, outcome: Outcome, reason: str) -> None:
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(outcome.lines, ["image_changed=true", f"reason={reason}"], outcome.stdout)

    def assertEqualPins(self, outcome: Outcome) -> None:
        self.assertEqual(outcome.code, 0, outcome.stderr)
        self.assertEqual(outcome.lines, ["image_changed=false", "reason=pin-equal"], outcome.stdout)


class ThePinComparison(ImageCase):
    def test_only_equal_pins_are_false(self) -> None:
        for name, current, baseline in (
            ("a and a", PIN_A, PIN_A),
            ("b and b", PIN_B, PIN_B),
            ("a with every other line changed", "stack-config-pin-a-other-lines-changed.yaml", PIN_A),
        ):
            with self.subTest(case=name):
                self.assertEqualPins(self.verdict(current, baseline))

    def test_an_image_only_change_asks_in_both_directions(self) -> None:
        # The case the whole script exists for: the Pulumi plan of such a push can
        # be all `same`, and the pin is the only thing that differs.
        for name, current, baseline in (("a to b", PIN_B, PIN_A), ("b to a", PIN_A, PIN_B)):
            with self.subTest(case=name):
                self.assertAsks(self.verdict(current, baseline), "pin-differs")

    def test_a_commented_out_pin_is_not_a_second_pin(self) -> None:
        self.assertEqualPins(self.verdict("stack-config-pin-a-other-lines-changed.yaml", "stack-config-pin-a-other-lines-changed.yaml"))

    def test_the_stdout_names_the_baseline_run_and_no_pin(self) -> None:
        outcome = self.verdict(PIN_B, PIN_A)
        self.assertIn("baseline_run=9002", outcome.stdout)
        for secret in ("sha256", "a1a1", "b2b2", TOKEN):
            self.assertNotIn(secret, outcome.stdout + outcome.stderr)


class TheLastDeployedRead(ImageCase):
    def test_it_reads_the_newest_earlier_successful_run_at_its_head_sha(self) -> None:
        # Run 12's commit holds pin A and run 10's holds pin B; the current pin is
        # B. Reading the newest earlier success gives a change; reading the older
        # one (or any other commit) would give none.
        contents = {BASELINE_SHA: (200, fixture(PIN_A)), OLDER_SHA: (200, fixture(PIN_B))}
        outcome = self.run_script(PIN_B, self.serve(contents=contents))
        self.assertAsks(outcome, "pin-differs")
        reads = [r for r in outcome.requests if r["path"] == CONTENTS_PATH]
        self.assertEqual([urllib.parse.parse_qs(r["query"])["ref"] for r in reads], [[BASELINE_SHA]])

    def test_the_baseline_comes_from_the_success_listing_alone(self) -> None:
        # Run 11 failed and sits between 10 and 12 in the unfiltered listing. The
        # success listing names run 10 only, so the baseline is run 10: a failed
        # or unlisted run is never read, whatever its commit holds.
        contents = {OLDER_SHA: (200, fixture(PIN_B)), "1111111111111111111111111111111111111111": (200, fixture(PIN_A))}
        outcome = self.run_script(PIN_B, self.serve(success=ok("runs-success-pending-12-baseline-10.json"), recent=ok("runs-recent-quiet.json"), contents=contents))
        self.assertEqualPins(outcome)

    def test_the_requests_are_reads_that_carry_the_token_in_a_header_only(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A)
        self.assertEqual(len(outcome.requests), 3)
        for request in outcome.requests:
            self.assertEqual(request["method"], "GET")
            self.assertEqual(request["authorization"], f"Bearer {TOKEN}")
            self.assertNotIn(TOKEN, request["path"] + request["query"])
        self.assertIn("application/vnd.github.raw", outcome.requests[2]["accept"])

    def test_it_asks_for_the_config_by_its_own_name_and_the_runs_of_the_workflow_on_main(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A)
        self.assertEqual({r["path"] for r in outcome.requests}, {RUNS_PATH, CONTENTS_PATH})


class EveryUnreadableInputAsks(ImageCase):
    """Each of these is the only wrong thing in an otherwise equal-pin run, so a `true` here is that input's doing."""

    def test_no_earlier_successful_run(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A, success=ok("runs-empty.json"), recent=ok("runs-recent-only-current.json"))
        self.assertAsks(outcome, "no-earlier-successful-run")

    def test_only_a_later_run_succeeded(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A, success=ok("runs-success-later-14-and-12.json"), recent=ok("runs-recent-later-14.json"))
        self.assertAsks(outcome, "later-run-succeeded")

    def test_an_earlier_run_still_pending(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A, success=ok("runs-success-pending-12-baseline-10.json"), recent=ok("runs-recent-pending.json"))
        self.assertAsks(outcome, "earlier-run-pending")

    def test_an_api_error_on_either_listing_or_the_contents(self) -> None:
        contents = {BASELINE_SHA: (200, fixture(PIN_A))}
        cases = (
            ("success listing 500", {"success": (500, b'{"message":"boom"}')}, "api-error"),
            ("recent listing 403", {"recent": (403, b'{"message":"forbidden"}')}, "api-error"),
            ("contents 404", {"contents": {}}, "api-error"),
        )
        for name, override, reason in cases:
            with self.subTest(case=name):
                kwargs = {"contents": contents, **override}
                outcome = self.run_script(PIN_A, self.serve(**kwargs))
                self.assertAsks(outcome, reason)

    def test_a_redirect_is_an_error_and_is_not_followed(self) -> None:
        api = self.serve(contents={BASELINE_SHA: (200, fixture(PIN_A))}, redirect_contents=True)
        outcome = self.run_script(PIN_A, api)
        self.assertAsks(outcome, "api-error")
        self.assertEqual([r["path"] for r in outcome.requests].count("/elsewhere"), 0)

    def test_a_reply_that_is_not_json(self) -> None:
        contents = {BASELINE_SHA: (200, fixture(PIN_A))}
        for name, override in (
            ("success", {"success": (200, b"<html>not json</html>")}),
            ("recent", {"recent": (200, b"")}),
            ("an object without workflow_runs", {"success": (200, b'{"total_count": 0}')}),
            ("a list", {"success": (200, b"[]")}),
        ):
            with self.subTest(case=name):
                outcome = self.run_script(PIN_A, self.serve(contents={**contents}, **override))
                self.assertAsks(outcome, "runs-unreadable")

    def test_a_run_order_that_cannot_be_established(self) -> None:
        contents = {BASELINE_SHA: (200, fixture(PIN_A))}
        for name, fixture_name in (
            ("run_number a string", "runs-success-string-run-number.json"),
            ("run_number missing", "runs-success-missing-run-number.json"),
            ("two runs with one number", "runs-success-duplicate-run-number.json"),
        ):
            with self.subTest(case=name):
                outcome = self.run_script(PIN_A, self.serve(success=ok(fixture_name), contents=contents))
                self.assertAsks(outcome, "run-order-unknown")

    def test_the_current_run_is_not_in_the_recent_listing(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A, recent=ok("runs-recent-without-current.json"))
        self.assertAsks(outcome, "current-run-unlocated")

    def test_the_run_number_the_runner_reports_is_not_the_one_the_api_holds(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A, env={"GITHUB_RUN_NUMBER": "14"})
        self.assertAsks(outcome, "current-run-unlocated")

    def test_a_listing_that_ignored_its_filter(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A, success=ok("runs-success-other-branch.json"))
        self.assertAsks(outcome, "runs-unreadable")

    def test_a_head_sha_that_is_not_a_commit(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A, success=ok("runs-success-bad-sha.json"))
        self.assertAsks(outcome, "runs-unreadable")

    def test_a_success_listing_entry_that_is_not_a_success(self) -> None:
        outcome = self.verdict(PIN_A, PIN_A, success=ok("runs-success-not-a-success.json"))
        self.assertAsks(outcome, "runs-unreadable")

    def test_a_missing_or_repeated_pin_line_at_the_baseline(self) -> None:
        for name, baseline in (
            ("missing", "stack-config-no-pin.yaml"),
            ("repeated", "stack-config-two-pins.yaml"),
            ("quoted", "stack-config-quoted-pin.yaml"),
            ("not utf-8", b"\xff\xfe\x00"),
            ("empty", b""),
        ):
            with self.subTest(case=name):
                outcome = self.verdict(PIN_A, baseline)
                self.assertEqual(outcome.lines[0], "image_changed=true")

    def test_a_missing_or_repeated_pin_line_in_the_current_config(self) -> None:
        for name, current in (
            ("missing", "stack-config-no-pin.yaml"),
            ("repeated", "stack-config-two-pins.yaml"),
            ("quoted", "stack-config-quoted-pin.yaml"),
        ):
            with self.subTest(case=name):
                outcome = self.verdict(current, PIN_A)
                self.assertEqual(outcome.lines[0], "image_changed=true")

    def test_an_unreadable_current_config(self) -> None:
        self.assertAsks(self.verdict(None, PIN_A), "config-unreadable")

    def test_a_missing_or_malformed_environment(self) -> None:
        for name, env in (
            ("no token", {"GITHUB_TOKEN": ""}),
            ("no repository", {"GITHUB_REPOSITORY": "not a repository"}),
            ("no run id", {"GITHUB_RUN_ID": None}),
            ("run number zero", {"GITHUB_RUN_NUMBER": "0"}),
            ("run number text", {"GITHUB_RUN_NUMBER": "thirteen"}),
            ("an insecure API address", {"GITHUB_API_URL": "http://api.example.invalid"}),
            ("an API address with a path", {"GITHUB_API_URL": "https://api.example.invalid/x"}),
        ):
            with self.subTest(case=name):
                outcome = self.verdict(PIN_A, PIN_A, env=env)
                self.assertAsks(outcome, "bad-environment")

    def test_an_argument_that_is_not_a_plain_file_name(self) -> None:
        outcome = self.run_script(PIN_A, self.serve(contents={BASELINE_SHA: (200, fixture(PIN_A))}), config_name="../Pulumi.blog.yaml")
        self.assertAsks(outcome, "bad-argument")

    def test_an_unwritable_output_fails_the_step_and_writes_no_verdict(self) -> None:
        outcome = self.run_script(PIN_A, self.serve(contents={BASELINE_SHA: (200, fixture(PIN_A))}), output_is_dir=True)
        self.assertEqual(outcome.code, 1)
        self.assertNotIn("image_changed=false", outcome.stdout)


class AnyExceptionAsks(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()
        self.env = {
            "GITHUB_REPOSITORY": REPOSITORY,
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_RUN_NUMBER": RUN_NUMBER,
        }

    def evaluate(self, fetch):
        with tempfile.TemporaryDirectory() as tmp:
            config = pathlib.Path(tmp) / CONFIG
            config.write_bytes(fixture(PIN_A))
            cwd = pathlib.Path.cwd()
            os.chdir(tmp)
            try:
                return self.module.evaluate(CONFIG, WORKFLOW, self.env, fetch)
            finally:
                os.chdir(cwd)

    def test_an_exception_from_the_fetch_is_a_true(self) -> None:
        for error in (RuntimeError("boom"), KeyError("k"), MemoryError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                def fetch(path, accept, error=error):
                    raise error

                verdict = self.evaluate(fetch)
                self.assertTrue(verdict.image_changed, verdict)
                self.assertEqual(verdict.reason, "internal-error")

    def test_the_equal_pin_answer_is_false_through_the_same_seam(self) -> None:
        def fetch(path, accept):
            if "contents" in path:
                return fixture(PIN_A)
            return fixture(SUCCESS if "status=success" in path else RECENT)

        verdict = self.evaluate(fetch)
        self.assertFalse(verdict.image_changed, verdict)

    def test_the_pin_reader_takes_only_one_shape(self) -> None:
        digest = "a1" * 32
        good = f"x:imageRef: ghcr.io/example/t@sha256:{digest}\n"
        self.assertEqual(self.module.pin_of(good), f"ghcr.io/example/t@sha256:{digest}")
        for bad in (
            "",
            "x:imageRef: ghcr.io/example/t@sha256:abc\n",
            "x:imageRef: ghcr.io/example/t:latest\n",
            f"x:imageRef: 'ghcr.io/example/t@sha256:{digest}'\n",
            f"x:imageRef: ghcr.io/example/t@sha256:{digest} # comment\n",
            f"x:imageRef: |\n  ghcr.io/example/t@sha256:{digest}\n",
            good + good,
            f"imageRef: ghcr.io/example/t@sha256:{digest}\n",
        ):
            with self.subTest(text=bad):
                with self.assertRaises(self.module.Unclear):
                    self.module.pin_of(bad)

    def test_the_fixture_digests_are_real_digests(self) -> None:
        for name in (PIN_A, PIN_B):
            self.assertIsNotNone(self.module.PIN_LINE.search(next(line for line in fixture(name).decode().splitlines() if "imageRef" in line)), name)


class TheFixturesAreJson(unittest.TestCase):
    def test_every_json_fixture_parses(self) -> None:
        for path in sorted(DATA.glob("*.json")):
            with self.subTest(fixture=path.name):
                json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
