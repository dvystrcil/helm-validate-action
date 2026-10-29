#!/usr/bin/env python3
"""Every `resources:` block in a values file reaches the chart's render.

    check-values-landing.py <values.yaml> <rendered.yaml> [<path> ...]

<path> arguments name `resources` blocks the chart renders some other way
than as a resources mapping (e.g. kube-prometheus-stack turns
prometheusOperator.prometheusConfigReloader.resources into operator flags),
as dotted paths: prometheusOperator.prometheusConfigReloader.resources.

Two checks, both on what helm cannot see:

1. Duplicate mapping keys anywhere in the values file. PyYAML (and helm's
   own parser) keep the last one without a word.
2. Each non-empty `resources:` mapping in the values file must equal (as
   quantities, allowing extra keys from chart defaults) a `resources:`
   mapping somewhere in the rendered manifests.

dvystrcil/prometheus#66: three blocks indented under commented-out parents
became duplicate keys under additionalPrometheusRulesMap.homelab-custom-rules.
The file was valid YAML, the chart has no schema to reject it, `helm template`
rendered cleanly, and Prometheus ran BestEffort for three months. Neither
check needs to know the chart: the render is the authority on what landed.

Exit 1 with one line per problem; VALUES-LANDING-COMPLETE on success.
"""
import re
import sys

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

SECTIONS = ("requests", "limits")
_BIN = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60}
_DEC = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1, "k": 1e3, "M": 1e6, "G": 1e9,
        "T": 1e12, "P": 1e15, "E": 1e18}
_QTY = re.compile(r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(Ki|Mi|Gi|Ti|Pi|Ei|[numkMGTPE]?)$")


def parse_quantity(name, value):
    """A Kubernetes quantity as a comparable number: millicores for cpu,
    base units for everything else. Unparseable values compare as strings."""
    m = _QTY.match(str(value).strip())
    if not m:
        return str(value)
    n = float(m.group(1)) * (_BIN.get(m.group(2)) or _DEC[m.group(2)])
    return round(n * 1000) if name == "cpu" else round(n)


def _normalize(block):
    """{"requests.cpu": 100, ...} for a resources mapping; {} if it sets nothing."""
    out = {}
    if not isinstance(block, dict):
        return out
    for section in SECTIONS:
        for name, value in (block.get(section) or {}).items() if isinstance(block.get(section), dict) else ():
            out[f"{section}.{name}"] = parse_quantity(name, value)
    return out


def _key(node):
    return node.value if isinstance(node, ScalarNode) else "?"


def find_duplicates(text):
    """'path' + lines for every mapping key that appears twice in one mapping."""
    out = []

    def walk(node, path):
        if isinstance(node, MappingNode):
            seen = {}
            for k, v in node.value:
                key = _key(k)
                line = k.start_mark.line + 1
                if key in seen and key != "<<":
                    p = ".".join(path + [key])
                    out.append(f"duplicate key {p} at line {line} (first at line {seen[key]}): "
                               f"YAML keeps only the last, the earlier one is silently dropped")
                seen.setdefault(key, line)
                walk(v, path + [key])
        elif isinstance(node, SequenceNode):
            for i, v in enumerate(node.value):
                walk(v, path[:-1] + [f"{path[-1]}[{i}]"] if path else [f"[{i}]"])

    for doc in yaml.compose_all(text):
        if doc is not None:
            walk(doc, [])
    return out


def values_resources(values):
    """[(dotted path to the resources key, normalized block)] for every
    non-empty resources mapping in the values tree."""
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{path}.{k}" if path else str(k)
                if k == "resources" and _normalize(v):
                    out.append((p, _normalize(v)))
                walk(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(values, "")
    return out


def rendered_resources(text):
    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "resources" and _normalize(v):
                    out.append(_normalize(v))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for doc in yaml.safe_load_all(text):
        walk(doc)
    return out


def _lands(block, rendered):
    return any(all(r.get(k) == v for k, v in block.items()) for r in rendered)


def check(values_text, rendered_text, elsewhere):
    problems = find_duplicates(values_text)
    blocks = values_resources(yaml.safe_load(values_text) or {})
    rendered = rendered_resources(rendered_text)
    declared = set(elsewhere)
    paths = {p for p, _ in blocks}
    for path, block in blocks:
        landed = _lands(block, rendered)
        if path in declared:
            if landed:
                problems.append(f"{path} is declared as rendered elsewhere, but it lands as a "
                                f"resources mapping: remove it from resources-rendered-elsewhere")
            continue
        if not landed:
            problems.append(f"{path} is set in values but not in the render: no rendered "
                            f"resources mapping equals {block}. The chart does not read this "
                            f"path (wrong key, or nested under a commented-out parent). If the "
                            f"chart renders it another way, list it in resources-rendered-elsewhere")
    for path in sorted(declared - paths):
        problems.append(f"{path} is declared in resources-rendered-elsewhere but the values file "
                        f"has no resources block there: remove the stale entry")
    return problems


def main(argv):
    if len(argv) < 3:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        values_text = f.read()
    with open(argv[2]) as f:
        rendered_text = f.read()
    elsewhere = argv[3:]
    problems = check(values_text, rendered_text, elsewhere)
    n = len(values_resources(yaml.safe_load(values_text) or {}))
    print(f"values-landing: {n} resources block(s) in {argv[1]}, "
          f"{len(elsewhere)} declared rendered elsewhere, {len(problems)} problem(s)")
    for p in problems:
        print(f"::error::{p}")
    if problems:
        return 1
    print("VALUES-LANDING-COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
