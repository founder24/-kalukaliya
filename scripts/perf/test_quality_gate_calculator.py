#!/usr/bin/env python3
"""Fixture-based smoke tests for quality_gate_calculator.py
(Task #361 §3.3). Run as: python3 -m unittest scripts.perf.test_quality_gate_calculator
or directly: python3 scripts/perf/test_quality_gate_calculator.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "quality_gate_calculator.py"


def _run(payload: dict) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        path = f.name
    try:
        r = subprocess.run(
            [sys.executable, str(SCRIPT), path],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode, r.stdout + r.stderr
    finally:
        Path(path).unlink(missing_ok=True)


class QualityGateCalculatorTests(unittest.TestCase):
    def test_promote_when_clearly_not_worse_and_cheaper(self):
        """Tight 1b advantage on rating + engagement + cost -> exit 0."""
        rc, out = _run({
            "window_days": 7,
            "1b": {"n": 12000, "ratings_mean": 4.30, "ratings_std": 0.50,
                   "followups": 5400, "cost_usd_total": 3.60},
            "3b": {"n": 12000, "ratings_mean": 4.20, "ratings_std": 0.50,
                   "followups": 4800, "cost_usd_total": 7.20},
        })
        self.assertEqual(rc, 0, msg=out)
        self.assertIn("PROMOTE 1b", out)

    def test_reject_when_1b_clearly_worse(self):
        """Large negative rating + engagement deltas -> exit 1."""
        rc, out = _run({
            "window_days": 7,
            "1b": {"n": 12000, "ratings_mean": 4.05, "ratings_std": 0.95,
                   "followups": 4500, "cost_usd_total": 3.60},
            "3b": {"n": 12000, "ratings_mean": 4.20, "ratings_std": 0.95,
                   "followups": 4800, "cost_usd_total": 7.20},
        })
        self.assertEqual(rc, 1, msg=out)
        self.assertIn("REJECT 1b", out)
        self.assertIn("FAIL (1b worse", out)

    def test_reject_when_1b_not_cheaper(self):
        """Even a tie on quality, if 1b isn't cheaper, reject."""
        rc, out = _run({
            "window_days": 7,
            "1b": {"n": 12000, "ratings_mean": 4.20, "ratings_std": 0.50,
                   "followups": 4800, "cost_usd_total": 7.30},
            "3b": {"n": 12000, "ratings_mean": 4.20, "ratings_std": 0.50,
                   "followups": 4800, "cost_usd_total": 7.20},
        })
        self.assertEqual(rc, 1, msg=out)
        self.assertIn("FAIL (1b not cheaper)", out)

    def test_insufficient_when_undersized_n(self):
        """n<10000 per arm -> exit 2 (no decision)."""
        rc, out = _run({
            "window_days": 7,
            "1b": {"n": 500, "ratings_mean": 4.20, "ratings_std": 0.95,
                   "followups": 200, "cost_usd_total": 0.15},
            "3b": {"n": 500, "ratings_mean": 4.18, "ratings_std": 0.97,
                   "followups": 198, "cost_usd_total": 0.30},
        })
        self.assertEqual(rc, 2, msg=out)
        self.assertIn("INSUFFICIENT", out)

    def test_insufficient_when_window_too_short(self):
        """window_days<7 -> exit 2 even with n>=10000."""
        rc, out = _run({
            "window_days": 3,
            "1b": {"n": 12000, "ratings_mean": 4.20, "ratings_std": 0.50,
                   "followups": 4800, "cost_usd_total": 3.60},
            "3b": {"n": 12000, "ratings_mean": 4.20, "ratings_std": 0.50,
                   "followups": 4800, "cost_usd_total": 7.20},
        })
        self.assertEqual(rc, 2, msg=out)
        self.assertIn("INSUFFICIENT", out)

    def test_invalid_when_missing_field(self):
        """Schema violation -> exit 3."""
        rc, out = _run({"window_days": 7,
                        "1b": {"n": 12000, "ratings_mean": 4.2,
                               "ratings_std": 0.5, "followups": 4800}})
        self.assertEqual(rc, 3, msg=out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
