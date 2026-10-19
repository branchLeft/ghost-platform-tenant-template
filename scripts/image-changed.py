#!/usr/bin/env python3
"""Decide whether the image this stack pins differs from the one last deployed:
only a proven-equal pin is `false`. The rules and their reasons are in
image-changed.md beside this file."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, NamedTuple

BRANCH = "main"
EVENT = "push"
PER_PAGE = 30
PENDING_STATUSES = ("waiting", "queued", "in_progress")
MAX_BODY = 1_048_576
TIMEOUT_SECONDS = 20
DEFAULT_API = "https://api.github.com"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")
# The only shape of pin that is compared: a plain scalar ending in a sha256
# digest. Anything else (quoted, a block scalar, a comment tail) is not read.
PIN_VALUE = r"[A-Za-z0-9][A-Za-z0-9._/:-]*@sha256:[0-9a-f]{64}"
PIN_LINE = re.compile(rf"[ \t]*[A-Za-z0-9_.-]+:imageRef:[ \t]+({PIN_VALUE})[ \t]*\Z")
PIN_KEY = "imageRef"

Fetch = Callable[[str, str], bytes]


class Verdict(NamedTuple):
    image_changed: bool
    reason: str
    baseline_run: int = 0


class Unclear(Exception):
    """An input is not something this script can rely on; `reason` is its code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise Unclear(reason)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(text: str | None, reason: str) -> int:
    _require(text is not None and re.fullmatch(r"[1-9][0-9]{0,15}", text) is not None, reason)
    return int(str(text))


def pin_of(text: str) -> str:
    """The one `imageRef` value in a stack config file, or Unclear.

    Every non-comment line naming the key must be that one line, in the one
    shape this reads, so a second, quoted or malformed line is never skipped."""
    named = [line for line in text.splitlines() if PIN_KEY in line and not line.lstrip().startswith("#")]
    _require(len(named) == 1, "pin-line-count")
    match = PIN_LINE.match(named[0])
    _require(match is not None, "pin-line-shape")
    return str(match.group(1))  # type: ignore[union-attr]


class Run(NamedTuple):
    id: int
    number: int
    sha: str
    status: str
    conclusion: str | None


class Listing(NamedTuple):
    runs: list[Run]
    total: int


def _runs(raw: bytes, reason: str, status: str | None = None) -> Listing:
    """The runs of a workflow-run listing; any entry this does not understand is Unclear.

    Every entry is held to the filter the request asked for (`status`, when one
    was asked: a run status, or `success` for a completed success), so a listing
    that ignored the filter is refused rather than read. `total` is the count
    GitHub says match, which can exceed the entries returned."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as error:  # a UnicodeDecodeError is a ValueError
        raise Unclear(reason) from error
    _require(isinstance(doc, dict) and isinstance(doc.get("workflow_runs"), list), reason)
    _require(_is_int(doc.get("total_count")) and doc["total_count"] >= len(doc["workflow_runs"]), reason)
    found = []
    for entry in doc["workflow_runs"]:
        _require(isinstance(entry, dict), reason)
        _require(_is_int(entry.get("id")) and _is_int(entry.get("run_number")), "run-order-unknown")
        _require(isinstance(entry.get("head_sha"), str) and SHA_PATTERN.match(entry["head_sha"]) is not None, reason)
        _require(isinstance(entry.get("status"), str), reason)
        _require(entry.get("conclusion") is None or isinstance(entry["conclusion"], str), reason)
        _require(entry.get("event") == EVENT and entry.get("head_branch") == BRANCH, reason)
        run = Run(entry["id"], entry["run_number"], entry["head_sha"], entry["status"], entry["conclusion"])
        if status == "success":
            _require(run.status == "completed" and run.conclusion == "success", reason)
        elif status is not None:
            _require(run.status == status, reason)
        found.append(run)
    return Listing(found, doc["total_count"])


def _consistent(runs: list[Run]) -> None:
    """No two runs share an id with different numbers, or a number with different ids."""
    by_id: dict[int, int] = {}
    by_number: dict[int, int] = {}
    for run in runs:
        _require(by_id.setdefault(run.id, run.number) == run.number, "run-order-unknown")
        _require(by_number.setdefault(run.number, run.id) == run.id, "run-order-unknown")


def evaluate(config_path: str, workflow: str, environ: dict[str, str], fetch: Fetch) -> Verdict:
    """The verdict for this checkout's stack config against the last deployed one.

    Fails closed: anything not understood, or any error, is a `true` with a reason code."""
    try:
        return _decide(config_path, workflow, environ, fetch)
    except Unclear as unclear:
        return Verdict(True, unclear.reason)
    except BaseException:  # noqa: BLE001 -- every failure must reach the gate
        return Verdict(True, "internal-error")


def _decide(config_path: str, workflow: str, environ: dict[str, str], fetch: Fetch) -> Verdict:
    _require(NAME_PATTERN.match(workflow) is not None and NAME_PATTERN.match(config_path) is not None, "bad-argument")
    repository = environ.get("GITHUB_REPOSITORY", "")
    _require(REPOSITORY_PATTERN.match(repository) is not None, "bad-environment")
    run_id = _positive_int(environ.get("GITHUB_RUN_ID"), "bad-environment")
    run_number = _positive_int(environ.get("GITHUB_RUN_NUMBER"), "bad-environment")

    try:
        with open(config_path, encoding="utf-8") as handle:
            current = pin_of(handle.read())
    except (OSError, UnicodeDecodeError) as error:
        raise Unclear("config-unreadable") from error

    base = f"/repos/{repository}/actions/workflows/{workflow}/runs?branch={BRANCH}&event={EVENT}&per_page={PER_PAGE}"
    json_type = "application/vnd.github+json"
    successes = _runs(fetch(base + "&status=success", json_type), "runs-unreadable", "success").runs
    recent = _runs(fetch(base, json_type), "runs-unreadable").runs
    pending = []
    for status in PENDING_STATUSES:
        listed = _runs(fetch(f"{base}&status={status}", json_type), "runs-unreadable", status)
        # An earlier run that has not finished may still deploy an older image
        # after this one, so no pin is trusted as the host's while one is pending.
        # Asked for by status, not read from the recent window, so an old one is seen.
        _require(not any(run.number < run_number for run in listed.runs), "earlier-run-pending")
        # A listing that is cut short may be hiding an earlier run past its end.
        _require(listed.total <= len(listed.runs), "pending-listing-truncated")
        pending += listed.runs
    _consistent(successes + recent + pending)

    mine = [run for run in recent if run.id == run_id]
    _require(len(mine) == 1 and mine[0].number == run_number, "current-run-unlocated")

    # The same rule for a status the three listings above do not name.
    _require(not any(run.number < run_number and run.status != "completed" for run in recent), "earlier-run-pending")
    # A later run that already succeeded has put its own image on the host.
    _require(not any(run.number > run_number and run.conclusion == "success" for run in successes), "later-run-succeeded")

    earlier = [run for run in successes if run.number < run_number]
    _require(bool(earlier), "no-earlier-successful-run")
    baseline = max(earlier, key=lambda run: run.number)
    _require(baseline.status == "completed" and baseline.conclusion == "success", "runs-unreadable")

    quoted = urllib.parse.quote(config_path, safe="")
    raw = fetch(f"/repos/{repository}/contents/{quoted}?ref={baseline.sha}", "application/vnd.github.raw+json")
    try:
        previous = pin_of(raw.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise Unclear("baseline-unreadable") from error

    if previous != current:
        return Verdict(True, "pin-differs", baseline.id)

    # A deploy that failed or was stopped after the baseline can leave the host
    # between a pin write and its restart, so the host may hold the pin of such a
    # run, not the baseline's. Every finished run after the baseline that is not
    # a success must carry the current pin too. The unfiltered window must reach
    # back to the baseline, or a run between could be out of sight.
    _require(baseline.id in {run.id for run in recent}, "history-window")
    between = [
        run
        for run in recent
        if baseline.number < run.number < run_number and run.status == "completed" and run.conclusion != "success"
    ]
    for sha in dict.fromkeys(run.sha for run in between):
        raw = fetch(f"/repos/{repository}/contents/{quoted}?ref={sha}", "application/vnd.github.raw+json")
        try:
            held = pin_of(raw.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise Unclear("intervening-run-unreadable") from error
        _require(held == current, "intervening-run-pin-differs")
    return Verdict(False, "pin-equal", baseline.id)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error: the token is sent to the API host and nowhere else."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
        return None


def make_fetch(api: str, token: str) -> Fetch:
    parsed = urllib.parse.urlsplit(api)
    secure = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS)
    if not secure or not parsed.hostname or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise Unclear("bad-environment")
    if not token:
        raise Unclear("bad-environment")
    root = f"{parsed.scheme}://{parsed.netloc}"
    opener = urllib.request.build_opener(_NoRedirect)

    def fetch(path: str, accept: str) -> bytes:
        request = urllib.request.Request(root + path, method="GET")
        request.add_unredirected_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", accept)
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                status = response.status
                body = response.read(MAX_BODY + 1)
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise Unclear("api-error") from error
        _require(status == 200 and len(body) <= MAX_BODY, "api-error")
        return body

    return fetch


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="image-changed.py", description=__doc__.split("\n")[0])
    parser.add_argument("--workflow", required=True, help="the workflow file name, e.g. infra-ci.yml")
    parser.add_argument("--config", required=True, help="the stack config file at the repository root")
    parser.add_argument("--github-output", required=True, help="append image_changed=<bool> and reason=<code> here")
    args = parser.parse_args(argv[1:])

    try:
        fetch = make_fetch(os.environ.get("GITHUB_API_URL", DEFAULT_API), os.environ.get("GITHUB_TOKEN", ""))
        verdict = evaluate(args.config, args.workflow, dict(os.environ), fetch)
    except Unclear as unclear:
        verdict = Verdict(True, unclear.reason)
    except BaseException:  # noqa: BLE001 -- every failure must reach the gate
        verdict = Verdict(True, "internal-error")

    text = "true" if verdict.image_changed else "false"
    try:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"image_changed={text}\nreason={verdict.reason}\n")
    except OSError as error:
        print(f"::error::cannot write the image verdict: {type(error).__name__}")
        return 1

    print(f"image_changed={text} reason={verdict.reason} baseline_run={verdict.baseline_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
