#!/usr/bin/env python3
"""Exit 0 only if a GitHub environment can be used by main alone.

Usage: assert-plan-environment-main-only.py ENV_JSON POLICIES_JSON | --self-test
Rules and the API shapes this reads: assert-plan-environment-main-only.md
"""
import json
import sys


def main_only(env, policies):
    """Return (ok, reason). Anything malformed is not main-only, and both
    documents must be readable: one failed API call fails the guard."""
    if not isinstance(env, dict) or not isinstance(policies, dict):
        return False, "an API response was unreadable"
    if "deployment_branch_policy" not in env or "branch_policies" not in policies:
        return False, "an API response lacked the expected field (error body?)"
    rule = env["deployment_branch_policy"]
    if not isinstance(rule, dict):
        return False, "the environment is open to every branch"
    if rule.get("custom_branch_policies") is not True or rule.get("protected_branches") is not False:
        return False, "the environment does not use a custom branch policy alone"
    listed = policies["branch_policies"]
    if not isinstance(listed, list) or len(listed) != 1:
        return False, "the custom branch policy does not name exactly one branch"
    only = listed[0]
    if not isinstance(only, dict) or only.get("name") != "main" or only.get("type", "branch") != "branch":
        return False, "the one branch the policy names is not the branch main"
    return True, "custom branch policy naming main alone"


ENV_MAIN = {"name": "production-plan", "deployment_branch_policy": {
    "protected_branches": False, "custom_branch_policies": True}}
ENV_OPEN = {"name": "production-plan", "deployment_branch_policy": None}
ENV_PROTECTED = {"name": "production-plan", "deployment_branch_policy": {
    "protected_branches": True, "custom_branch_policies": False}}
POL_MAIN = {"total_count": 1, "branch_policies": [{"id": 1, "name": "main", "type": "branch"}]}
POL_MAIN_UNTYPED = {"total_count": 1, "branch_policies": [{"id": 1, "name": "main"}]}
POL_WILDCARD = {"total_count": 1, "branch_policies": [{"id": 2, "name": "*", "type": "branch"}]}
POL_PREFIX = {"total_count": 1, "branch_policies": [{"id": 2, "name": "ma*", "type": "branch"}]}
POL_TAG = {"total_count": 1, "branch_policies": [{"id": 3, "name": "main", "type": "tag"}]}
POL_TWO = {"total_count": 2, "branch_policies": [
    {"id": 1, "name": "main", "type": "branch"}, {"id": 4, "name": "dev", "type": "branch"}]}
POL_NONE = {"total_count": 0, "branch_policies": []}

CASES = [
    ("main alone passes", ENV_MAIN, POL_MAIN, True),
    ("main alone, type omitted, passes", ENV_MAIN, POL_MAIN_UNTYPED, True),
    ("open to every branch fails", ENV_OPEN, POL_NONE, False),
    ("protected branches only fails", ENV_PROTECTED, POL_NONE, False),
    ("wildcard fails", ENV_MAIN, POL_WILDCARD, False),
    ("prefix wildcard fails", ENV_MAIN, POL_PREFIX, False),
    ("a tag named main fails", ENV_MAIN, POL_TAG, False),
    ("two branches fail", ENV_MAIN, POL_TWO, False),
    ("custom policy with no branch fails", ENV_MAIN, POL_NONE, False),
    ("unreadable env fails", None, POL_MAIN, False),
    ("unreadable policies fail", ENV_MAIN, None, False),
    ("api error body fails", {"message": "Not Found"}, {"message": "Not Found"}, False),
]


def self_test():
    bad = 0
    for name, env, policies, want in CASES:
        got, _ = main_only(env, policies)
        print("%s: %s" % ("ok  " if got == want else "FAIL", name))
        bad += got != want
    return 1 if bad else 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    docs = []
    for path in argv:
        try:
            with open(path) as f:
                docs.append(json.load(f))
        except (OSError, ValueError) as exc:
            print("::error::could not read %s: %s" % (path, exc), file=sys.stderr)
            return 1
    ok, reason = main_only(*docs)
    print(("main only: " if ok else "NOT main only: ") + reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
