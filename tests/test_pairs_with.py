#!/usr/bin/env python3
"""pairs-with.py: which argocd-projects PR this app-repo PR is paired with
(dvystrcil/homelab#2166).

A chart bump that needs a values change is two PRs. This action read the
Application from argocd-projects `main`, so the values PR validated against
the OLD pin and passed (immich#59), while the pin PR failed against the old
values (argocd-projects#341). `Pairs-With: dvystrcil/argocd-projects#N` in
the app PR's body makes the action read the Application at that PR's head.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "pairs-with.py"
REPO = "dvystrcil/argocd-projects"


def run(args, stdin):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin,
                          capture_output=True, text=True)


class FindTest(unittest.TestCase):

    def test_slug_form(self):
        r = run(["find", REPO], "values for 0.13.4\nPairs-With: dvystrcil/argocd-projects#341\n")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "341"))

    def test_url_form_and_case(self):
        r = run(["find", REPO], "pairs-with: https://github.com/dvystrcil/argocd-projects/pull/341\r\n")
        self.assertEqual(r.stdout.strip(), "341")

    def test_no_pairing_prints_nothing_and_succeeds(self):
        r = run(["find", REPO], "See dvystrcil/argocd-projects#341 for the pin.\n")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_pairing_with_another_repo_is_not_ours(self):
        """An app PR may pair with something else (another app repo); only a
        pairing with the projects repo moves the Application ref."""
        r = run(["find", REPO], "Pairs-With: dvystrcil/immich#59\n")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_two_projects_pairings_is_an_error(self):
        r = run(["find", REPO], "Pairs-With: dvystrcil/argocd-projects#1\n"
                                "Pairs-With: dvystrcil/argocd-projects#2\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("more than one", r.stderr)

    def test_empty_body(self):
        r = run(["find", REPO], "")
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class HeadTest(unittest.TestCase):

    def test_open_pr_head(self):
        r = run(["head"], json.dumps({"state": "open", "merged_at": None, "head": {"sha": "abc"}}))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "abc"))

    def test_merged_pr_head(self):
        r = run(["head"], json.dumps({"state": "closed", "merged_at": "2026-11-12T00:00:00Z",
                                      "head": {"sha": "def"}}))
        self.assertEqual(r.stdout.strip(), "def")

    def test_closed_unmerged_is_an_error(self):
        """That pin never deploys; validating against it proves nothing."""
        r = run(["head"], json.dumps({"state": "closed", "merged_at": None, "head": {"sha": "x"}}))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("closed without merging", r.stderr)


if __name__ == "__main__":
    unittest.main()
