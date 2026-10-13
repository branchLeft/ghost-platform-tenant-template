#!/usr/bin/env python3
"""Print a tenant stack's `pulumi preview --json` plan, safely, or refuse it.

Usage: summarise-plan.py PREVIEW_JSON | --self-test
Rules and the line another tool reads: summarise-plan.md
"""
import json
import re
import sys

# What a resource type or name may contain on its way into a log. Anything else
# becomes `?`: a newline followed by `::error::` would be read by the runner as
# a workflow command, and `=` could forge the summary line below.
SAFE = re.compile(r"[^A-Za-z0-9_.:$/@+-]")
REFUSED_WORDS = ("delete", "replace")


class Unreadable(Exception):
    pass


def clean(text):
    return SAFE.sub("?", text)


def parse(raw):
    """Return (steps, summary), or raise Unreadable. A plan that cannot be read
    is never treated as an empty one."""
    if not isinstance(raw, dict):
        raise Unreadable("the plan is not a JSON object")
    steps, summary = raw.get("steps"), raw.get("changeSummary")
    if not isinstance(steps, list) or not steps:
        raise Unreadable("the plan has no steps (a preview with no steps is not a preview of this stack)")
    if not isinstance(summary, dict) or not summary:
        raise Unreadable("the plan has no changeSummary")
    for key, count in summary.items():
        if not isinstance(key, str) or isinstance(count, bool) or not isinstance(count, int):
            raise Unreadable("the plan's changeSummary is not a map of step kind to count")
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("op"), str):
            raise Unreadable("a plan step has no operation")
        if not isinstance(step.get("urn"), str):
            raise Unreadable("a plan step has no URN")
    return steps, summary


def type_and_name(urn):
    """The resource type and name of a URN, without its stack and project."""
    parts = urn.split("::")
    if len(parts) < 4:
        return "?", "?"
    return clean(parts[-2]), clean(parts[-1])


def refused(steps):
    return [s for s in steps if any(w in s["op"].lower() for w in REFUSED_WORDS)]


def render(steps, summary):
    """The lines to print for a plan that passed `refused`."""
    shown = {k: v for k, v in sorted(summary.items())}
    lines = [
        "OK: no protected Hetzner resource is destroyed by this plan "
        "(%d steps checked, changeSummary=%s)." % (len(steps), shown)
    ]
    changed = [s for s in steps if s["op"] != "same"]
    lines.append("Steps other than same: %d" % len(changed))
    for step in changed:
        kind, name = type_and_name(step["urn"])
        lines.append("  %s %s %s" % (clean(step["op"]), kind, name))
    return lines


def self_test():
    urn = "urn:pulumi:t::p::ghost:tenant:GhostTenant::t"
    same = {"op": "same", "urn": urn}
    cases = [
        ("same plan passes", {"steps": [same], "changeSummary": {"same": 1}}, True),
        ("update passes", {"steps": [dict(same, op="update")], "changeSummary": {"update": 1}}, True),
        ("create is shown, not refused here",
         {"steps": [dict(same, op="create")], "changeSummary": {"create": 1}}, True),
        ("delete refuses", {"steps": [dict(same, op="delete")], "changeSummary": {"delete": 1}}, False),
        ("replace refuses", {"steps": [dict(same, op="replace")], "changeSummary": {"replace": 1}}, False),
        ("create-replacement refuses",
         {"steps": [dict(same, op="create-replacement")], "changeSummary": {"create": 1}}, False),
        ("delete-replaced refuses",
         {"steps": [dict(same, op="delete-replaced")], "changeSummary": {"delete": 1}}, False),
        ("no steps is unreadable", {"steps": [], "changeSummary": {"same": 1}}, None),
        ("no summary is unreadable", {"steps": [same]}, None),
        ("a boolean count is unreadable", {"steps": [same], "changeSummary": {"same": True}}, None),
        ("not an object is unreadable", [], None),
    ]
    bad = 0
    for name, plan, want in cases:
        try:
            steps, summary = parse(plan)
            got = not refused(steps)
        except Unreadable:
            got = None
        print("%s: %s" % ("ok  " if got == want else "FAIL", name))
        bad += got != want
    return 1 if bad else 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as f:
            steps, summary = parse(json.load(f))
    except (OSError, ValueError, Unreadable) as exc:
        print("::error::could not read the plan: %s" % clean(str(exc)), file=sys.stderr)
        return 2
    found = refused(steps)
    if found:
        for step in found:
            kind, name = type_and_name(step["urn"])
            print("REFUSED: %s %s %s" % (clean(step["op"]), kind, name), file=sys.stderr)
        print("::error::this plan would delete or replace a resource; nothing has been applied.", file=sys.stderr)
        return 1
    print("\n".join(render(steps, summary)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
