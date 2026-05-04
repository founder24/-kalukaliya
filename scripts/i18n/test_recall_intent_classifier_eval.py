#!/usr/bin/env python3
"""Smoke tests for recall_intent_classifier_eval.py
(Task #362 §1.3 / §1.5). Run as:
  python3 scripts/i18n/test_recall_intent_classifier_eval.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "recall_intent_classifier_eval.py"


def _run(rows: list[dict],
         extra_args: list[str] | None = None) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
        path = f.name
    try:
        cmd = [sys.executable, str(SCRIPT), "--labels", path]
        if extra_args:
            cmd.extend(extra_args)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode, r.stdout + r.stderr
    finally:
        Path(path).unlink(missing_ok=True)


class RecallIntentEvalTests(unittest.TestCase):
    def test_passes_when_clear_signal(self):
        rows = ([{"label": "yes", "prompt": p} for p in [
                    "earlier you said photosynthesis happens in chloroplasts",
                    "go back to that thing about Newton",
                    "what did I ask about earlier",
                    "@recall the formula for water",
                    "you mentioned mitosis",
                    "remember when we discussed the constitution",
                    "the thing about photosynthesis again",
                    "previously, you said something",
                    "as you said earlier",
                    "remember the answer about velocity"]] +
                [{"label": "no", "prompt": p} for p in [
                    "what is photosynthesis",
                    "explain newton's first law",
                    "give me three examples of acids",
                    "how do plants make food",
                    "describe the structure of an atom",
                    "what is the capital of assam",
                    "list the fundamental rights",
                    "solve x squared equals 9",
                    "define velocity",
                    "name the first prime minister of india"]])
        rc, out = _run(rows)
        self.assertEqual(rc, 0, msg=out)
        self.assertIn("PASS", out)

    def test_fails_when_recall_phrasing_missing(self):
        rows = ([{"label": "yes", "prompt": p} for p in [
                    "weird made-up sentence one",
                    "totally novel utterance two",
                    "no recall keyword here three",
                    "another phrasing four",
                    "and a fifth"]] +
                [{"label": "no", "prompt": p} for p in [
                    "what is photosynthesis", "explain newton",
                    "what are acids", "how plants make food",
                    "structure of atom"]])
        rc, out = _run(rows)
        self.assertEqual(rc, 1, msg=out)
        self.assertIn("FAIL", out)

    def test_recall_prefix_always_fires(self):
        rows = [{"label": "yes", "prompt": "@recall what was the formula"},
                {"label": "no", "prompt": "what is calcium oxide"}]
        rc, out = _run(rows, ["--target-recall", "1.0"])
        self.assertEqual(rc, 0, msg=out)

    def test_invalid_label_set_no_positives(self):
        rows = [{"label": "no", "prompt": "what is X"} for _ in range(5)]
        rc, out = _run(rows)
        self.assertEqual(rc, 2, msg=out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
