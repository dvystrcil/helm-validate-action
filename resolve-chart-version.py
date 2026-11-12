#!/usr/bin/env python3
"""Print the chart version an ArgoCD Application pins (homelab#1443 option A).

    resolve-chart-version.py <application.yaml> <chart-name> [<chart-repo>]

The Application's targetRevision is what the cluster deploys. Reading it
directly, instead of a `.chart-version` copy in the app repo, removes the
second pin that could drift from it -- the drift that let the cluster
upgrade prometheus, infisical and vaultwarden while CI kept validating the
old chart (homelab architecture/chart-version-source-of-truth.md).

Exits non-zero with a reason on anything short of exactly one version: a
validator that falls back to a default version validates a chart nobody
runs, and reports it green.
"""
import sys

import yaml


class ResolveError(Exception):
    pass


def _sources(spec: dict) -> list:
    # Both shapes: handling only `sources:` would skip single-source apps.
    if isinstance(spec.get("sources"), list):
        return [s for s in spec["sources"] if isinstance(s, dict)]
    if isinstance(spec.get("source"), dict):
        return [spec["source"]]
    return []


def _match(source: dict, chart: str):
    """The repo URL to report if `source` deploys `chart`, else None.

    Two shapes. A classic Helm repo names the chart in `chart:`. ArgoCD's
    native OCI form puts the chart in the repoURL and sets `chart: '.'`
    (oci://ghcr.io/immich-app/immich-charts/immich); its URL is reported
    without the chart segment so it compares to the action's chart-repo."""
    name = str(source.get("chart", "")).strip()
    url = str(source.get("repoURL", "")).strip()
    if name == chart:
        return url
    if name == "." and url.startswith("oci://"):
        base, _, last = url.rstrip("/").rpartition("/")
        if last == chart:
            return base
    return None


def resolve(text: str, chart: str) -> tuple[str, str]:
    """(targetRevision, repoURL) of the one source whose `chart:` is `chart`."""
    # BaseLoader keeps every scalar a string: SafeLoader would read an
    # unquoted `1.30` as the float 1.3 and validate the wrong chart.
    found = []
    for doc in yaml.load_all(text, Loader=yaml.BaseLoader):
        if not isinstance(doc, dict) or not isinstance(doc.get("spec"), dict):
            continue
        for s in _sources(doc["spec"]):
            url = _match(s, chart)
            if url is None:
                continue
            rev = str(s.get("targetRevision", "")).strip()
            if not rev:
                raise ResolveError(f"chart: {chart} has no targetRevision")
            if "{{" in rev:
                raise ResolveError(
                    f"chart: {chart} targetRevision is templated ({rev}); "
                    "point argocd-application at a concrete Application")
            found.append((rev, url))
    if not found:
        raise ResolveError(f"no source with chart: {chart}")
    if len({rev for rev, _ in found}) > 1:
        raise ResolveError(
            f"ambiguous: chart: {chart} is pinned to "
            f"{sorted({rev for rev, _ in found})}")
    return found[0]


def same_repo(a: str, b: str) -> bool:
    return a.strip().rstrip("/") == b.strip().rstrip("/")


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    path, chart = argv[1], argv[2]
    try:
        with open(path) as f:
            rev, url = resolve(f.read(), chart)
    except (OSError, yaml.YAMLError, ResolveError) as e:
        print(f"::error::{path}: {e}", file=sys.stderr)
        return 1
    if len(argv) == 4 and url and not same_repo(url, argv[3]):
        # Not fatal: a mirror can serve the same chart. But helm renders
        # from chart-repo, so a version only the gate's repo has fails there.
        print(f"::warning::{path} pulls {chart} from {url}, "
              f"but chart-repo is {argv[3]}", file=sys.stderr)
    print(rev)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
