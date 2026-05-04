#!/usr/bin/env python3
"""
Quality-gate calculator — Task #361 §3.3.

Computes the statistical-not-worse check for the fast-mode 1b vs 3b
A/B promotion gate. Two metrics are tested:

  1. user_rating_delta — normal-approximation 95% CI on the difference
     of means using Welch's standard-error formula
     (sqrt(var_a/n_a + var_b/n_b)) and z=1.96. With the enforced
     n>=10000 per arm, the t-distribution critical value collapses
     onto z=1.96 to four decimals, so the normal approximation is
     used for simplicity. Promotion-allowed iff the lower bound of
     the 95% CI on (mean_1b - mean_3b) is >= 0.

  2. engagement_delta — two-proportion normal-approximation z-interval
     on the follow-up-within-60s rates. Promotion-allowed iff the
     lower bound of the 95% CI on (p_1b - p_3b) is >= 0.

A third gate (cost_delta < 0) is a simple comparison and is checked
inline.

Promotion is granted ONLY if all three gates pass AND both arms have
n >= 10000 AND the experiment has run >= 7 days.

Inputs are read from a JSON file with the schema:
  {
    "window_days": 7,
    "1b": {"n": 12345, "ratings": [int...], "followups": int, "cost_usd_total": float},
    "3b": {"n": 12500, "ratings": [int...], "followups": int, "cost_usd_total": float}
  }
The "ratings" arrays may be omitted if "ratings_mean" + "ratings_std"
are provided directly (useful when reading from App Insights aggregates
where raw arrays aren't exported).

Exit codes:
  0  — promotion APPROVED  (all gates pass)
  1  — promotion REJECTED  (at least one gate fails)
  2  — INSUFFICIENT data   (sample size or window too small to decide)
  3  — INVALID input

Usage:
  python3 scripts/perf/quality_gate_calculator.py results.json
  python3 scripts/perf/quality_gate_calculator.py --min-n 10000 --min-days 7 results.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path


def _welch_ci(mean_a: float, var_a: float, n_a: int,
              mean_b: float, var_b: float, n_b: int,
              z: float = 1.96) -> tuple[float, float]:
    """Two-sided 95% CI on (mean_a - mean_b) using Welch's SE
    (sqrt(var_a/n_a + var_b/n_b)) and a normal-approximation critical
    value (z=1.96 for 95%). With n>=10000 per arm the t critical
    value collapses onto z to four decimals."""
    se = math.sqrt(var_a / n_a + var_b / n_b)
    diff = mean_a - mean_b
    return (diff - z * se, diff + z * se)


def _proportion_ci(succ_a: int, n_a: int, succ_b: int, n_b: int,
                   z: float = 1.96) -> tuple[float, float]:
    """Two-sided 95% CI on (p_a - p_b) via two-proportion normal-
    approximation z-interval."""
    p_a = succ_a / n_a
    p_b = succ_b / n_b
    se = math.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b)
    diff = p_a - p_b
    return (diff - z * se, diff + z * se)


def _arm_stats(arm: dict) -> tuple[int, float, float, int, float]:
    """Returns (n, mean, var, followups, cost_total)."""
    n = int(arm["n"])
    if "ratings" in arm and arm["ratings"]:
        ratings = arm["ratings"]
        mean = statistics.fmean(ratings)
        var = statistics.variance(ratings) if len(ratings) > 1 else 0.0
    else:
        mean = float(arm["ratings_mean"])
        std = float(arm["ratings_std"])
        var = std * std
    followups = int(arm["followups"])
    cost = float(arm["cost_usd_total"])
    return n, mean, var, followups, cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results_file", type=Path)
    ap.add_argument("--min-n", type=int, default=10000)
    ap.add_argument("--min-days", type=int, default=7)
    args = ap.parse_args()

    if not args.results_file.exists():
        print(f"ERROR: {args.results_file} not found", file=sys.stderr)
        return 3
    try:
        data = json.loads(args.results_file.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 3

    try:
        window_days = int(data["window_days"])
        n_1b, mean_1b, var_1b, fu_1b, cost_1b = _arm_stats(data["1b"])
        n_3b, mean_3b, var_3b, fu_3b, cost_3b = _arm_stats(data["3b"])
    except (KeyError, ValueError, TypeError) as e:
        print(f"ERROR: invalid schema: {e}", file=sys.stderr)
        return 3

    print(f"Window: {window_days} days")
    print(f"  1b: n={n_1b}, mean_rating={mean_1b:.4f}, var={var_1b:.4f}, "
          f"followups={fu_1b} ({100*fu_1b/n_1b:.2f}%), "
          f"cost_per_turn=${cost_1b/n_1b:.6f}")
    print(f"  3b: n={n_3b}, mean_rating={mean_3b:.4f}, var={var_3b:.4f}, "
          f"followups={fu_3b} ({100*fu_3b/n_3b:.2f}%), "
          f"cost_per_turn=${cost_3b/n_3b:.6f}")
    print()

    if n_1b < args.min_n or n_3b < args.min_n or window_days < args.min_days:
        print(f"INSUFFICIENT: need n>={args.min_n} per arm AND "
              f"window>={args.min_days} days. Got n_1b={n_1b}, "
              f"n_3b={n_3b}, days={window_days}.")
        return 2

    rating_lo, rating_hi = _welch_ci(mean_1b, var_1b, n_1b,
                                      mean_3b, var_3b, n_3b)
    rating_pass = rating_lo >= 0
    print(f"Gate 1 — user_rating_delta:    "
          f"95% CI [{rating_lo:+.4f}, {rating_hi:+.4f}]  "
          f"-> {'PASS (1b not-worse)' if rating_pass else 'FAIL (1b worse at 95% CI)'}")

    eng_lo, eng_hi = _proportion_ci(fu_1b, n_1b, fu_3b, n_3b)
    eng_pass = eng_lo >= 0
    print(f"Gate 2 — engagement_delta:     "
          f"95% CI [{eng_lo:+.4f}, {eng_hi:+.4f}]  "
          f"-> {'PASS (1b not-worse)' if eng_pass else 'FAIL (1b worse at 95% CI)'}")

    cost_delta = (cost_1b / n_1b) - (cost_3b / n_3b)
    cost_pass = cost_delta < 0
    print(f"Gate 3 — cost_per_turn_delta:  "
          f"${cost_delta:+.6f}  "
          f"-> {'PASS (1b cheaper)' if cost_pass else 'FAIL (1b not cheaper)'}")
    print()

    all_pass = rating_pass and eng_pass and cost_pass
    if all_pass:
        print("DECISION: PROMOTE 1b -> fast-mode primary.")
        print("Record this decision in infra/credit-burn-runbook.md §F.3.")
        return 0
    print("DECISION: REJECT 1b. Close the experiment, keep 3b as fast-mode primary.")
    print("Per #361 §3.3: no 'let's run it longer' — Workers-AI quota for "
          "the experiment is finite.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
