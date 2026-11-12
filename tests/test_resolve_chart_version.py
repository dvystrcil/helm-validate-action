"""Tests for resolve-chart-version.py (homelab#1443 option A).

Run: python3 -m unittest discover -s tests
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "resolve", ROOT / "resolve-chart-version.py")
resolve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolve)

MULTI = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: prometheus
spec:
  sources:
  # the chart
  - repoURL: 'https://prometheus-community.github.io/helm-charts'
    chart: kube-prometheus-stack
    targetRevision: '91.7.0'
    helm:
      valueFiles:
      - $values/values.yaml
  - repoURL: 'https://github.com/dvystrcil/prometheus.git'
    targetRevision: HEAD
    ref: values
"""

SINGLE = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
spec:
  source:
    targetRevision: 2.20.0
    repoURL: https://bitnami-labs.github.io/sealed-secrets
    chart: sealed-secrets
"""


class ResolveTest(unittest.TestCase):
    def test_multi_source_picks_the_chart_source_not_the_git_one(self):
        v, _ = resolve.resolve(MULTI, "kube-prometheus-stack")
        self.assertEqual(v, "91.7.0")

    def test_single_source_shape_is_read(self):
        # handling only `sources:` would silently skip every single-source app
        v, _ = resolve.resolve(SINGLE, "sealed-secrets")
        self.assertEqual(v, "2.20.0")

    def test_returns_the_gate_repo_url(self):
        _, url = resolve.resolve(MULTI, "kube-prometheus-stack")
        self.assertEqual(url, "https://prometheus-community.github.io/helm-charts")

    def test_unquoted_numeric_revision_stays_a_string(self):
        # YAML reads a bare 1.30 as the float 1.3 -- a version must not lose digits
        text = SINGLE.replace("2.20.0", "1.30")
        v, _ = resolve.resolve(text, "sealed-secrets")
        self.assertEqual(v, "1.30")

    def test_chart_absent_is_an_error_not_a_default(self):
        with self.assertRaisesRegex(resolve.ResolveError, "no source with chart: loki"):
            resolve.resolve(MULTI, "loki")

    def test_chart_without_target_revision_is_an_error(self):
        text = MULTI.replace("    targetRevision: '91.7.0'\n", "")
        with self.assertRaisesRegex(resolve.ResolveError, "no targetRevision"):
            resolve.resolve(text, "kube-prometheus-stack")

    def test_two_different_pins_for_one_chart_is_ambiguous(self):
        text = MULTI + SINGLE.replace("sealed-secrets", "kube-prometheus-stack") \
            .replace("apiVersion", "---\napiVersion", 1)
        with self.assertRaisesRegex(resolve.ResolveError, "ambiguous"):
            resolve.resolve(text, "kube-prometheus-stack")

    def test_same_pin_twice_is_not_ambiguous(self):
        text = MULTI + "---\n" + MULTI
        v, _ = resolve.resolve(text, "kube-prometheus-stack")
        self.assertEqual(v, "91.7.0")

    def test_templated_revision_is_refused(self):
        # an ApplicationSet template is not a version helm can fetch
        text = SINGLE.replace("2.20.0", "'{{.version}}'")
        with self.assertRaisesRegex(resolve.ResolveError, "templated"):
            resolve.resolve(text, "sealed-secrets")

    def test_commented_out_source_is_not_a_pin(self):
        text = MULTI.replace("kube-prometheus-stack", "x") + \
            "  # - chart: kube-prometheus-stack\n  #   targetRevision: 1.0.0\n"
        with self.assertRaises(resolve.ResolveError):
            resolve.resolve(text, "kube-prometheus-stack")


OCI = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
spec:
  sources:
  - repoURL: 'oci://ghcr.io/immich-app/immich-charts/immich'
    chart: '.'
    targetRevision: 0.13.4
    helm:
      valueFiles:
      - $values/values.yaml
  - repoURL: 'https://github.com/dvystrcil/immich.git'
    targetRevision: HEAD
    ref: values
"""


class OciTest(unittest.TestCase):
    """ArgoCD's native OCI form: the repoURL names the chart and `chart: '.'`
    (the shape dvystrcil/argocd-projects' ARC Applications already use)."""

    def test_oci_dot_source_is_found_by_its_last_path_segment(self):
        self.assertEqual(resolve.resolve(OCI, "immich"),
                         ("0.13.4", "oci://ghcr.io/immich-app/immich-charts"))

    def test_oci_url_returned_without_the_chart_so_it_compares_to_chart_repo(self):
        _, url = resolve.resolve(OCI, "immich")
        self.assertTrue(resolve.same_repo(url, "oci://ghcr.io/immich-app/immich-charts/"))

    def test_oci_dot_source_for_another_chart_is_not_a_match(self):
        with self.assertRaises(resolve.ResolveError):
            resolve.resolve(OCI, "immich-charts")

    def test_https_dot_source_is_not_treated_as_oci(self):
        text = OCI.replace("oci://ghcr.io", "https://ghcr.io")
        with self.assertRaises(resolve.ResolveError):
            resolve.resolve(text, "immich")

class RepoUrlTest(unittest.TestCase):
    def test_trailing_slash_is_not_a_mismatch(self):
        self.assertTrue(resolve.same_repo("https://a.io/charts/", "https://a.io/charts"))

    def test_different_host_is_a_mismatch(self):
        self.assertFalse(resolve.same_repo("https://a.io/charts", "https://b.io/charts"))


if __name__ == "__main__":
    unittest.main()
