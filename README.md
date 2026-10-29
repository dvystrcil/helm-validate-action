# helm-validate-action

PR-time validation for repos that supply a `values.yaml` to a remote Helm
chart pinned in a **different** repo — this homelab's split-repo pattern
(the chart version lives in an ArgoCD `Application`'s `sources[]`, the
`values.yaml` lives in a per-app repo like `dvystrcil/cert-manager`).
Sibling to [`dvystrcil/kustomize-validate-action`](https://github.com/dvystrcil/kustomize-validate-action).

## Why this exists

Renovate tracks a raw image tag override in `values.yaml` (a literal
string it can regex) but has no idea a separate repo pins the actual
Helm chart version — and nothing checks whether `values.yaml` is still
valid for whichever chart version is actually pinned. Both gaps surfaced
reviewing `dvystrcil/cert-manager#2`: a Renovate PR bumped just the
controller's image tag, leaving the chart (and therefore
webhook/cainjector/startupapicheck, which derive their images from the
chart's `appVersion`) two minor versions behind — and separately,
`values.yaml` still had three keys the chart's `v1.21.0` release removed,
which would have failed Helm's schema validation on any future chart
bump regardless of whether those keys' features were even enabled.

`helm template --version <pinned> -f values.yaml` catches the second
problem directly (a chart's `values.schema.json`, when it ships one,
rejects removed/renamed/type-changed keys with a specific error naming
the exact path) — no changelog-reading required.

## Reading the version from the Application (v2, preferred)

Since v2 the action can read the pin straight from the ArgoCD
`Application` — the one that actually deploys — instead of a copy:

```yaml
jobs:
  helm:
    # The homelab's <repo>-helm-runner sets mount a read-only argocd-projects
    # token, minted every 20 min by cluster-ops' argocd-projects-token.
    runs-on: prometheus-helm-runner
    steps:
      - uses: actions/checkout@v7
      - uses: dvystrcil/helm-validate-action@v2
        with:
          chart-repo: https://prometheus-community.github.io/helm-charts
          chart-name: kube-prometheus-stack
          argocd-application: prometheus/prometheus.yaml
          github-token-file: /etc/argocd-projects-token/token
```

It uses the `targetRevision` of the source whose `chart:` equals
`chart-name` (`source:` or `sources:`), and fails rather than guessing
when there is no such source, no `targetRevision`, a templated one, or two
different pins. It needs `python3` with PyYAML (the homelab ARC runner
image has both). A `repoURL` that differs from `chart-repo` is a warning.

Why: two pins drift, and the dangerous direction is the gate moving ahead
of the shadow — the cluster upgrades while CI keeps validating the old
chart. It happened three times by 2026-09-26 (homelab
`architecture/chart-version-source-of-truth.md`, homelab#1443). Migrate a
repo by switching to `@v2`, adding these two inputs, and deleting its
`.chart-version` plus the Renovate rule that bumps it. Without
`argocd-application`, v2 behaves exactly like v1.

## The `.chart-version` convention (v1, being retired)

The chart version pin lives in two places by design:

1. **The ArgoCD `Application`** (`argocd-projects/<app>/<app>.yaml`,
   `sources[].targetRevision`) — the value ArgoCD actually syncs against.
2. **A `.chart-version` file in the values-owning repo** (this action reads
   it) — a duplicate of (1), tracked here so CI can validate `values.yaml`
   against it without a cross-repo fetch.

**Use the exact chart version string** (matching the chart repo's index,
usually `vX.Y.Z` for jetstack charts) — `helm template --version` falls
back to "closest available" on a near-miss instead of failing outright,
which defeats the point of pinning.

Wire a Renovate `customManagers` entry to bump `.chart-version` from the
same `datasource: helm` used for the image-tag bump, grouped into one PR
— see `dvystrcil/cert-manager`'s `renovate.json` for the reference
config. This solves the "two PRs to juggle, nothing catches when they
drift" complaint that motivated this action, not just the schema check.

**If `.chart-version` and the ArgoCD Application's `targetRevision` ever
disagree**, that's a bug to fix by hand — this action doesn't reach across
repos to auto-detect drift between the two, only to validate `values.yaml`
against whichever version `.chart-version` says.

## Usage

```yaml
name: validate
on: [pull_request]
jobs:
  helm:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: dvystrcil/helm-validate-action@v1
        with:
          chart-repo: https://charts.jetstack.io
          chart-name: cert-manager
```

Inputs:

| Input | Default | Description |
|---|---|---|
| `chart-repo` | *required* | Helm chart repository URL |
| `chart-name` | *required* | Chart name within that repo |
| `chart-version-file` | `.chart-version` | File with the pinned chart version |
| `argocd-application` | *(empty)* | v2: Application path in `argocd-projects-repo`; when set, `chart-version-file` is ignored |
| `github-token` | *(empty)* | v2: token that can read `argocd-projects-repo` |
| `github-token-file` | *(empty)* | v2: file holding that token; fails clearly if empty or past a sibling `expires_at` |
| `argocd-projects-repo` | `dvystrcil/argocd-projects` | v2: repo holding the Applications |
| `argocd-projects-ref` | `main` | v2: ref to read (main is what deploys) |
| `values-file` | `values.yaml` | Values file to validate |
| `helm-set` | *(empty)* | Extra `--set`/`--set-string` args, rarely needed |
| `resources-rendered-elsewhere` | *(empty)* | Dotted paths of `resources:` blocks the chart renders some other way than as a resources mapping (see below) |

## Outputs

| output | what |
|---|---|
| `chart-version` | the version this run validated against, as resolved |
| `rendered-file` | path of the `helm template` output (release name `test-release`) |

Use them for any extra assertion on the render instead of restating the chart
pin in your own workflow. A second pin drifts: dvystrcil/longhorn's render
check hard-coded 1.12.0 for two weeks while the cluster ran 1.13.0.

```yaml
- id: helm
  uses: dvystrcil/helm-validate-action@v2
  with: { ... }
- run: python3 check.py "${{ steps.helm.outputs.rendered-file }}"
```

## Values reach the render

After `helm template`, the action checks the values file against what it
rendered (`check-values-landing.py`). It runs on every consumer and has no
off switch:

1. **No duplicate mapping keys**, anywhere in the file. YAML (PyYAML and
   helm alike) keeps the last one and drops the rest without a word.
2. **Every non-empty `resources:` block lands.** Each one must equal a
   `resources:` mapping somewhere in the render, comparing quantities by
   value (`1Gi` = `1024Mi`, `1` = `1000m`) and allowing extra keys from
   chart defaults.

Why: in dvystrcil/prometheus#66 three `resources:` blocks were indented under
commented-out parents, so YAML nested them as duplicate keys under
`additionalPrometheusRulesMap.homelab-custom-rules`. The file was valid YAML,
kube-prometheus-stack has no values schema to reject it, `helm template`
rendered cleanly, and Prometheus ran BestEffort for three months. A block at
a plausible but wrong path (`prometheus.resources` instead of
`prometheus.prometheusSpec.resources`) fails the same way, with no
duplicate key to give it away. The check needs no per-chart knowledge: the
render is the authority on what landed.

**A chart that renders resources some other way** (kube-prometheus-stack
turns `prometheusOperator.prometheusConfigReloader.resources` into
`--config-reloader-*` operator flags) needs that path declared:

```yaml
- uses: dvystrcil/helm-validate-action@v2
  with:
    # ...
    resources-rendered-elsewhere: prometheusOperator.prometheusConfigReloader.resources
```

Declarations are checked in both directions. An entry naming no block
fails as stale, and an entry whose block does land as a mapping fails as
unnecessary, so the list cannot quietly excuse the next block written at a
path that stopped being special.

## What this deliberately does NOT do

- **Doesn't sync `.chart-version` with the live ArgoCD Application** —
  that's still a human (or Renovate) job; this only validates internal
  consistency of what's committed here.
- **Doesn't catch every possible breaking change** — only ones the
  chart's own `values.schema.json` encodes as a schema violation. RBAC
  changes, behavior changes, and anything not expressed as a values-shape
  change (like cert-manager v1.21.0's two RBAC-permission changes) still
  need a human to read the release notes. Schema validation is a floor,
  not a ceiling.
- **No cluster access, no dry-run against live state** — same rationale
  as `kustomize-validate-action`: a PR-time gate that authorizes with
  write verbs is a bigger blast radius than this is worth; this only
  ever talks to the public chart repository.

## Provenance

Built 2026-08-01, surfaced by reviewing `dvystrcil/cert-manager#2`
(controller image bump) and finding the chart-version/values-schema gap
by hand. See `homelab#816` for the full design writeup.
