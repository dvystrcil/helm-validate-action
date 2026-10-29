"""Tests for check-values-landing.py (dvystrcil/homelab#1830 AC2).

Run: python3 -m unittest discover -s tests

dvystrcil/prometheus#66: three `resources:` blocks were indented under
commented-out parents, so YAML nested them as duplicate keys under
`additionalPrometheusRulesMap.homelab-custom-rules`. PyYAML kept the last,
the chart read none, and Prometheus ran BestEffort for three months while
`helm template` rendered cleanly. A values file can be valid YAML, pass the
chart's schema, and still configure nothing.
"""
import importlib.util
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "landing", ROOT / "check-values-landing.py")
landing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(landing)


def d(s):
    return textwrap.dedent(s)


# What a chart renders: two containers and a PVC template.
RENDER = d("""\
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: app
    spec:
      template:
        spec:
          containers:
          - name: app
            resources:
              requests:
                cpu: 100m
                memory: 2Gi
              limits:
                memory: 5Gi
          - name: sidecar
            resources:
              requests:
                cpu: 10m
                memory: 32Mi
    ---
    apiVersion: apps/v1
    kind: StatefulSet
    metadata:
      name: db
    spec:
      volumeClaimTemplates:
      - spec:
          resources:
            requests:
              storage: 30Gi
    """)


def problems(values, render=RENDER, elsewhere=()):
    return landing.check(d(values), render, list(elsewhere))


class TestDuplicateKeys(unittest.TestCase):
    def test_issue_66_shape_is_reported_with_path_and_lines(self):
        p = problems("""\
            additionalPrometheusRulesMap:
              homelab-custom-rules:
                groups: []
                # prometheusSpec:
                resources:
                  requests:
                    memory: 2Gi
                # alertmanagerSpec:
                resources:
                  requests:
                    memory: 64Mi
            """)
        dup = [x for x in p if "duplicate key" in x]
        self.assertEqual(len(dup), 1, p)
        self.assertIn("additionalPrometheusRulesMap.homelab-custom-rules.resources", dup[0])
        self.assertIn("line 9", dup[0])
        self.assertIn("line 5", dup[0])

    def test_duplicates_anywhere_are_reported_not_only_resources(self):
        p = problems("""\
            image:
              tag: "1.0"
              tag: "2.0"
            """)
        self.assertTrue(any("duplicate key" in x and "image.tag" in x for x in p), p)

    def test_no_duplicates_is_clean(self):
        self.assertEqual(problems("a:\n  b: 1\nc:\n  b: 2\n"), [])


class TestLanding(unittest.TestCase):
    def test_block_at_the_chart_path_lands(self):
        self.assertEqual(problems("""\
            app:
              resources:
                requests: {cpu: 100m, memory: 2Gi}
                limits: {memory: 5Gi}
            """), [])

    def test_block_at_a_wrong_path_is_reported(self):
        # Valid YAML, no duplicates, no schema to reject it: only the
        # render can tell that nothing reads `app.container.resources`.
        p = problems("""\
            app:
              container:
                resources:
                  requests: {cpu: 250m, memory: 3Gi}
                  limits: {memory: 6Gi}
            """)
        self.assertEqual(len(p), 1, p)
        self.assertIn("app.container.resources", p[0])
        self.assertIn("not in the render", p[0])

    def test_quantities_compare_by_value_not_spelling(self):
        self.assertEqual(problems("""\
            app:
              resources:
                requests: {cpu: "0.1", memory: 2048Mi}
                limits: {memory: 5120Mi}
            """), [])

    def test_chart_defaults_may_add_keys_the_values_block_lacks(self):
        # Values set only the limit; the rendered block also carries
        # requests from the chart's defaults. Still landed.
        self.assertEqual(problems("""\
            app:
              resources:
                limits: {memory: 5Gi}
            """), [])

    def test_a_value_that_differs_is_not_landed(self):
        p = problems("""\
            app:
              resources:
                requests: {cpu: 100m, memory: 2Gi}
                limits: {memory: 4Gi}
            """)
        self.assertEqual(len(p), 1, p)

    def test_non_container_resources_land_too(self):
        self.assertEqual(problems("""\
            db:
              storageSpec:
                volumeClaimTemplate:
                  spec:
                    resources:
                      requests: {storage: 30Gi}
            """), [])

    def test_empty_and_non_quantity_resources_are_ignored(self):
        # `resources: {}` sets nothing; RBAC rules use `resources:` as a list.
        self.assertEqual(problems("""\
            app:
              resources: {}
            rbac:
              rules:
              - apiGroups: [""]
                resources: [pods]
                verbs: [get]
            """), [])

    def test_blocks_inside_lists_are_checked(self):
        p = problems("""\
            extraContainers:
            - name: helper
              resources:
                requests: {cpu: 7m, memory: 7Mi}
            """)
        self.assertEqual(len(p), 1, p)
        self.assertIn("extraContainers[0].resources", p[0])


class TestRenderedElsewhere(unittest.TestCase):
    # kube-prometheus-stack renders prometheusConfigReloader.resources as
    # operator flags (--config-reloader-memory-limit=64Mi), not as a
    # resources mapping, so it needs declaring.
    RELOADER = """\
        prometheusOperator:
          prometheusConfigReloader:
            resources:
              requests: {cpu: 10m, memory: 32Mi}
              limits: {memory: 64Mi}
        """
    PATH = "prometheusOperator.prometheusConfigReloader.resources"

    def test_undeclared_args_rendered_block_is_reported(self):
        p = problems(self.RELOADER)
        self.assertEqual(len(p), 1, p)
        self.assertIn(self.PATH, p[0])

    def test_declared_path_passes(self):
        self.assertEqual(problems(self.RELOADER, elsewhere=[self.PATH]), [])

    def test_stale_declaration_is_reported(self):
        # Drift cuts both ways: a declaration for a block that is gone would
        # silently excuse the next block written at that path.
        p = problems("a: 1\n", elsewhere=[self.PATH])
        self.assertEqual(len(p), 1, p)
        self.assertIn("no resources block", p[0])

    def test_declaration_for_a_block_that_lands_is_reported(self):
        p = problems("""\
            app:
              resources:
                requests: {cpu: 100m, memory: 2Gi}
            """, elsewhere=["app.resources"])
        self.assertEqual(len(p), 1, p)
        self.assertIn("remove it", p[0])


class TestMain(unittest.TestCase):
    """The exit code and completion marker are what the action step gates on."""

    def run_main(self, values, elsewhere=()):
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            v, r = Path(t, "values.yaml"), Path(t, "rendered.yaml")
            v.write_text(d(values))
            r.write_text(RENDER)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = landing.main(["x", str(v), str(r), *elsewhere])
        return rc, out.getvalue()

    def test_problem_exits_1_without_completion_marker(self):
        rc, out = self.run_main("a:\n  b: 1\n  b: 2\n")
        self.assertEqual(rc, 1)
        self.assertIn("::error::duplicate key a.b", out)
        self.assertNotIn("VALUES-LANDING-COMPLETE", out)

    def test_clean_exits_0_with_completion_marker(self):
        rc, out = self.run_main("app:\n  resources:\n    limits: {memory: 5Gi}\n")
        self.assertEqual(rc, 0)
        self.assertIn("1 resources block(s)", out)
        self.assertIn("VALUES-LANDING-COMPLETE", out)


class TestQuantities(unittest.TestCase):
    def test_memory_suffixes(self):
        q = landing.parse_quantity
        self.assertEqual(q("memory", "1Gi"), q("memory", "1024Mi"))
        self.assertEqual(q("memory", "1G"), q("memory", "1000M"))
        self.assertEqual(q("memory", 134217728), q("memory", "128Mi"))

    def test_cpu_is_millicores(self):
        q = landing.parse_quantity
        self.assertEqual(q("cpu", 1), q("cpu", "1000m"))
        self.assertEqual(q("cpu", "0.5"), q("cpu", "500m"))

    def test_unparseable_falls_back_to_the_string(self):
        self.assertEqual(landing.parse_quantity("nvidia.com/gpu", "x"), "x")


if __name__ == "__main__":
    unittest.main()
