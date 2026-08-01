"""Assert the Layer B + C validator's per-check verdicts on the reference fixture.

Expected outcomes are transcribed from PRD §10 for the checks in scope. Run with:
    python -m unittest discover -s tests            (from schema_pipeline/)
or  python tests/test_reference_fixture.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_HERE)          # schema_pipeline/
sys.path.insert(0, _PKG_ROOT)               # -> import qa
sys.path.insert(0, _HERE)                   # -> import fixture

import fixture  # noqa: E402
from qa import pkg_ingest, validate  # noqa: E402

# id -> expected severity (PRD §10, Layer B + C subset).
EXPECTED = {
    # structural
    "D2": "PASS",
    # Layer B
    "B1.1": "ERROR",       # "chunk_0000_raw" is not a UUID
    "B1.2": "PASS",
    "B1.3": "PASS",
    "B1.4": "PASS",
    "B1.5": "PASS",
    "B1.6": "ERROR",       # RGB camera absent from config
    "B1.7": "FORMAT_GAP",  # task description has no field
    "B1.8": "ERROR",       # "Unknown_Catagory" sentinel
    "B1.9": "PASS",
    "B1.10": "FORMAT_GAP",
    "B1.11": "FORMAT_GAP",
    "B2.1": "PASS",
    "B2.2": "PASS",
    "B2.3": "PASS",
    "B2.4": "ERROR",       # UTC anchor ~9s off episode_start_time
    "B2.5": "FORMAT_GAP",
    "B2.6": "FORMAT_GAP",
    "B2.7": "FORMAT_GAP",
    # Layer C
    "C1": "PASS",          # all streams 245 frames
    "C2": "ERROR",         # 8.13 vs 9.11 vs 60
    "C3": "PASS",          # IMU covers video span
    "C4": "ERROR",         # 0 of 245 depth files present
    "C5": "WARN",          # two videos, none authoritative
    "C6": "WARN",          # RGB/depth registration undeclared
    "C7": "WARN",          # hands enabled but all frames empty
}


class ReferenceFixtureTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        fixture.build_reference_package(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _report(self):
        packages = pkg_ingest.discover_packages(self.root)
        self.assertEqual(len(packages), 1)
        # A fixed "now" just after the episode keeps B1.3 deterministic.
        result = validate.run(packages, now_unix=fixture.START_UNIX + 3600,
                              validated_at="2026-07-24T09:00:00Z")
        return result["packages"][0]

    def test_per_check_severities(self):
        rep = self._report()
        by_id = {c["id"]: c for c in rep["checks"]}
        for cid, want in EXPECTED.items():
            self.assertIn(cid, by_id, f"check {cid} was not emitted")
            got = by_id[cid]["severity"]
            self.assertEqual(got, want,
                             f"{cid} ({by_id[cid]['name']}): expected {want}, got {got} "
                             f"-- {by_id[cid]['message']}")

    def test_every_check_emits_a_record(self):
        # PRD §11.6: no silent passes -- every scoped check must appear.
        rep = self._report()
        emitted = {c["id"] for c in rep["checks"]}
        self.assertTrue(set(EXPECTED).issubset(emitted),
                        f"missing checks: {set(EXPECTED) - emitted}")

    def test_every_check_has_spec_ref(self):
        # PRD §11.4: every record cites the clause it enforces.
        rep = self._report()
        for c in rep["checks"]:
            self.assertTrue(c.get("spec_ref"), f"{c['id']} has no spec_ref")

    def test_verdict_is_reject(self):
        rep = self._report()
        self.assertEqual(rep["verdict"], "REJECT")
        self.assertGreater(rep["counts"]["ERROR"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
