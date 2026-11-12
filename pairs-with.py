#!/usr/bin/env python3
"""Which argocd-projects PR an app-repo PR is paired with (homelab#2166).

    pairs-with.py find <projects-repo>  < pr-body     # prints N, or nothing
    pairs-with.py head                  < pr.json     # prints the head SHA

A chart bump that needs a values change is two PRs: the pin in
argocd-projects and the values here. Reading the Application from
argocd-projects `main` validated the values PR against the OLD pin, so it
passed while the pin PR failed against the old values. A body line

    Pairs-With: dvystrcil/argocd-projects#341

points the action at that PR's head instead. Only a line that STARTS with
the key counts: PR bodies cite related PRs constantly. Stdlib only.
"""
import json
import re
import sys

_PAIR = re.compile(
    r"^\s*pairs-with:\s*(?:https://github\.com/)?"
    r"(?P<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:#|/pull/)(?P<n>\d+)\s*$",
    re.IGNORECASE | re.MULTILINE)


def find(body: str, projects_repo: str) -> list[int]:
    return [int(m.group("n"))
            for m in _PAIR.finditer(body.replace("\r\n", "\n"))
            if m.group("slug").lower() == projects_repo.lower()]


def head(pr: dict) -> str:
    if pr.get("state") == "closed" and not pr.get("merged_at"):
        raise SystemExit("pairs-with: the paired PR was closed without merging; "
                         "its pin never deploys")
    return pr["head"]["sha"]


def main(argv: list[str]) -> int:
    if argv[:1] == ["find"] and len(argv) == 2:
        ns = find(sys.stdin.read(), argv[1])
        if len(ns) > 1:
            print(f"pairs-with: more than one {argv[1]} pairing: {ns}", file=sys.stderr)
            return 1
        if ns:
            print(ns[0])
        return 0
    if argv == ["head"]:
        try:
            print(head(json.load(sys.stdin)))
        except SystemExit as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
