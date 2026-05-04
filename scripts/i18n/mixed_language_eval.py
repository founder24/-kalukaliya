#!/usr/bin/env python3
"""
Mixed-language eval harness — Task #362 §2.4.

Reads the labeled JSONL validation set
(`tests/i18n/mixed_language_eval.jsonl` by default) and computes
the three #362 §2.2 signals per row plus aggregate:

  Signal 1 — round-trip semantic preservation
             (cosine similarity between expected_answer_scope_en
             embedding and round-tripped English answer embedding)
  Signal 2 — script-purity rate (Unicode-block ratio against the
             intended output script)
  Signal 3 — engagement / rating placeholders (stubbed; require
             production telemetry — operator joins offline)

This harness has TWO modes:

  --dry-run (default)  Reads pre-baked golden translations + golden
                       cosine values from the JSONL row itself
                       (`golden_output_text`, `golden_round_trip_en`,
                       `golden_round_trip_cosine`). No network calls.
                       Used by CI on every change to the Indic-chain
                       config or fixture. Rows missing the golden
                       fields are skipped and reported.

  --live               Calls a translator endpoint (env var
                       INDIC_TRANSLATE_URL) and an embedder endpoint
                       (env var BGE_M3_EMBED_URL) over plain HTTP
                       POST. Operator runs this manually before
                       promoting an Indic-chain config change.
                       Network failures degrade gracefully (per-row
                       SKIPPED), but if > 20% of rows skip, the
                       whole run exits non-zero.

Thresholds (per #362 §2.2):
  - cosine p50 >= 0.80 (target)
  - cosine p50 <  0.70 → fail-the-smoke (broken route)
  - script-purity >= 0.95 (target)

Exit codes:
  0 — all targets met
  1 — one or more thresholds breached
  2 — bad input / harness failure
  3 — usage error
"""

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Reuse the script-purity computer from the sibling module.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
try:
    from script_purity_check import compute_purity  # type: ignore
except ImportError:
    print("ERROR: script_purity_check.py must live next to this script",
          file=sys.stderr)
    sys.exit(2)

DEFAULT_LABELS = "tests/i18n/mixed_language_eval.jsonl"
COSINE_TARGET = 0.80
COSINE_FAIL_FLOOR = 0.70
PURITY_TARGET = 0.95
LIVE_MAX_SKIP_RATE = 0.20


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _http_post_json(url: str, payload: dict, timeout: float = 10.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _live_translate(text: str, src: str, tgt: str) -> str:
    url = os.environ.get("INDIC_TRANSLATE_URL", "").strip()
    if not url:
        raise RuntimeError("INDIC_TRANSLATE_URL unset")
    resp = _http_post_json(
        url, {"text": text, "source_lang": src, "target_lang": tgt})
    out = resp.get("translation") or resp.get("text") or ""
    if not isinstance(out, str) or not out.strip():
        raise RuntimeError(f"empty translation from {url}")
    return out


def _live_embed(text: str) -> list[float]:
    url = os.environ.get("BGE_M3_EMBED_URL", "").strip()
    if not url:
        raise RuntimeError("BGE_M3_EMBED_URL unset")
    resp = _http_post_json(url, {"text": text, "model": "bge-m3"})
    vec = resp.get("embedding") or resp.get("vector")
    if not isinstance(vec, list) or not vec:
        raise RuntimeError(f"empty embedding from {url}")
    return [float(x) for x in vec]


def _eval_row_dry_run(row: dict) -> tuple[str, dict]:
    needed = ("golden_output_text", "golden_round_trip_cosine")
    missing = [k for k in needed if k not in row]
    if missing:
        return ("skipped", {"reason": f"missing golden fields: {missing}"})
    cosine = float(row["golden_round_trip_cosine"])
    purity, _, total_letters = compute_purity(
        row["golden_output_text"], row["output_lang"])
    if total_letters == 0:
        return ("skipped",
                {"reason": "golden_output_text has no scriptable letters"})
    return ("scored", {"cosine": cosine, "purity": purity})


def _eval_row_live(row: dict) -> tuple[str, dict]:
    try:
        if row["output_lang"] != row["input_lang"]:
            output_text = _live_translate(
                row["input_text"], row["input_lang"], row["output_lang"])
        else:
            output_text = row["input_text"]
        if row["output_lang"] == "en":
            round_trip_en = output_text
        else:
            round_trip_en = _live_translate(
                output_text, row["output_lang"], "en")
        v_expected = _live_embed(row["expected_answer_scope_en"])
        v_round_trip = _live_embed(round_trip_en)
        cosine = _cosine(v_expected, v_round_trip)
        purity, _, total_letters = compute_purity(
            output_text, row["output_lang"])
        if total_letters == 0:
            return ("skipped",
                    {"reason": "live output_text has no scriptable letters"})
        return ("scored",
                {"cosine": cosine, "purity": purity,
                 "output_text": output_text, "round_trip_en": round_trip_en})
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, RuntimeError, ValueError) as e:
        return ("skipped", {"reason": f"live call failed: {e}"})


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", default=DEFAULT_LABELS,
                   help=f"Validation set JSONL. Default {DEFAULT_LABELS}.")
    p.add_argument("--live", action="store_true",
                   help="Call live translator + embedder endpoints "
                        "(env vars INDIC_TRANSLATE_URL + BGE_M3_EMBED_URL "
                        "required). Default is dry-run using golden "
                        "fields embedded in the JSONL.")
    p.add_argument("--cosine-target", type=float, default=COSINE_TARGET)
    p.add_argument("--cosine-fail-floor", type=float,
                   default=COSINE_FAIL_FLOOR)
    p.add_argument("--purity-target", type=float, default=PURITY_TARGET)
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    if not Path(args.labels).exists():
        print(f"ERROR: labels file not found: {args.labels}",
              file=sys.stderr)
        return 3

    rows: list[dict] = []
    with open(args.labels) as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"ERROR: line {i}: {e}", file=sys.stderr)
                return 2

    if not rows:
        print("ERROR: no rows in labels file", file=sys.stderr)
        return 2

    cosines: list[float] = []
    purities: list[float] = []
    skipped: list[tuple[str, str]] = []

    for row in rows:
        for k in ("id", "input_lang", "output_lang",
                  "input_text", "expected_answer_scope_en"):
            if k not in row:
                skipped.append((row.get("id", "?"),
                                f"missing required field: {k}"))
                break
        else:
            status, result = (_eval_row_live(row) if args.live
                              else _eval_row_dry_run(row))
            if status == "scored":
                cosines.append(result["cosine"])
                purities.append(result["purity"])
                if not args.quiet:
                    print(f"  {row['id']}: cosine={result['cosine']:.3f} "
                          f"purity={result['purity']:.3f}")
            else:
                skipped.append((row["id"], result["reason"]))
                if not args.quiet:
                    print(f"  {row['id']}: SKIPPED — {result['reason']}")

    n_scored = len(cosines)
    n_skipped = len(skipped)
    print()
    print(f"n_total={len(rows)} n_scored={n_scored} n_skipped={n_skipped}")
    for sid, reason in skipped:
        print(f"  skipped: {sid}: {reason}")

    if n_scored == 0:
        print("ERROR: zero rows scored; cannot evaluate", file=sys.stderr)
        return 2

    cosine_p50 = _percentile(cosines, 0.50)
    cosine_p25 = _percentile(cosines, 0.25)
    purity_p50 = _percentile(purities, 0.50)
    purity_min = min(purities)
    print(f"cosine: p25={cosine_p25:.3f} p50={cosine_p50:.3f}")
    print(f"purity: min={purity_min:.3f} p50={purity_p50:.3f}")

    pass_cosine_target = cosine_p50 >= args.cosine_target
    fail_cosine_floor = cosine_p50 < args.cosine_fail_floor
    pass_purity = purity_p50 >= args.purity_target
    skip_rate = n_skipped / len(rows)
    fail_skip_rate = args.live and skip_rate > LIVE_MAX_SKIP_RATE

    print()
    print(f"Targets: cosine p50 >= {args.cosine_target:.2f} "
          f"(fail-floor < {args.cosine_fail_floor:.2f}); "
          f"script-purity p50 >= {args.purity_target:.2f}")
    print(f"  cosine p50:    {cosine_p50:.3f} -> "
          f"{'FAIL (broken route)' if fail_cosine_floor else ('PASS' if pass_cosine_target else 'WARN (under target)')}")
    print(f"  purity p50:    {purity_p50:.3f} -> "
          f"{'PASS' if pass_purity else 'FAIL'}")
    if args.live:
        print(f"  live skip-rate {skip_rate:.2%} -> "
              f"{'FAIL (>20%)' if fail_skip_rate else 'PASS'}")

    if fail_cosine_floor or not pass_purity or fail_skip_rate:
        return 1
    if not pass_cosine_target:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
