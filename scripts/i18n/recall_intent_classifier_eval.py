#!/usr/bin/env python3
"""
Recall-intent classifier evaluator — Task #362 §1.3 / §1.5.

Evaluates the two-tier recall-intent detector against a hand-labeled
JSONL set. Computes precision / recall / F1 per tier and combined.

Tier 1 = phrase-list match OR `@recall` prefix (sub-millisecond).
Tier 2 trigger = at least one anaphoric token present (necessary
condition for the LLM classifier to fire). The actual LLM call is
NOT made here — operators can layer the LLM result onto the JSONL
as a `tier2_classifier_yes: true` field for end-to-end evaluation;
without it, we report Tier 2 *trigger* precision/recall (upper
bound on the combined detector).

JSONL row schema (one row per line):
  {
    "label": "yes" | "no",       # ground-truth recall-intent label
    "prompt": "string",          # the user message
    "tier2_classifier_yes": bool # OPTIONAL: when present, treat as
                                 # the actual LLM classifier verdict
  }

Targets per spec: combined recall >= 0.85, combined FPR <= 0.15.

Usage:
  recall_intent_classifier_eval.py \\
    --labels tests/i18n/recall_intent_eval.jsonl \\
    --phrases scripts/i18n/recall_intent_tier1_phrases.json \\
    --tokens  scripts/i18n/recall_intent_tier2_tokens.json

Defaults to the seed phrase / token lists embedded below if the
files don't exist.

Exit codes:
  0 — combined recall >= 0.85 AND combined FPR <= 0.15
  1 — one or both targets missed
  2 — bad input / no positives in label set
  3 — usage error
"""

import argparse
import json
import re
import sys
from pathlib import Path

SEED_TIER1_PHRASES = [
    "earlier you said", "earlier you mentioned", "you mentioned",
    "you told me", "go back to", "what did i ask", "what did i say",
    "previously", "last time", "remember when", "remember the",
    "the thing about", "the part about", "as you said",
    "you said earlier",
]

SEED_TIER2_TOKENS = [
    "it", "that", "those", "then", "the same", "the one",
    "again", "still", "before",
]


def _load_json_or_seed(path: str | None, seed: list[str]) -> list[str]:
    if path and Path(path).exists():
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError(f"{path} must be a JSON list of strings")
        return [x.lower() for x in data]
    return [x.lower() for x in seed]


def tier1_hit(prompt: str, phrases: list[str]) -> bool:
    p = prompt.lower()
    if p.lstrip().startswith("@recall"):
        return True
    return any(phrase in p for phrase in phrases)


def tier2_trigger(prompt: str, tokens: list[str]) -> bool:
    p = prompt.lower()
    for tok in tokens:
        if " " in tok:
            if tok in p:
                return True
        else:
            if re.search(rf"\b{re.escape(tok)}\b", p):
                return True
    return False


def evaluate(rows: list[dict], phrases: list[str],
             tokens: list[str]) -> dict:
    n_pos = sum(1 for r in rows if r["label"] == "yes")
    n_neg = sum(1 for r in rows if r["label"] == "no")
    if n_pos == 0:
        raise ValueError("label set has no positives")

    t1_tp = t1_fp = t1_fn = 0
    t2_tp = t2_fp = t2_fn = 0
    combined_tp = combined_fp = combined_fn = 0

    for r in rows:
        is_pos = r["label"] == "yes"
        t1 = tier1_hit(r["prompt"], phrases)
        t2_trig = tier2_trigger(r["prompt"], tokens)
        if "tier2_classifier_yes" in r:
            t2_pred = t2_trig and bool(r["tier2_classifier_yes"])
        else:
            t2_pred = t2_trig
        combined = t1 or (not t1 and t2_pred)

        if t1 and is_pos: t1_tp += 1
        elif t1 and not is_pos: t1_fp += 1
        elif not t1 and is_pos: t1_fn += 1

        if t2_pred and is_pos: t2_tp += 1
        elif t2_pred and not is_pos: t2_fp += 1
        elif not t2_pred and is_pos: t2_fn += 1

        if combined and is_pos: combined_tp += 1
        elif combined and not is_pos: combined_fp += 1
        elif not combined and is_pos: combined_fn += 1

    def _stats(tp: int, fp: int, fn: int, n_neg: int) -> dict:
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)
        fpr = fp / n_neg if n_neg > 0 else 0.0
        return {"precision": precision, "recall": recall,
                "f1": f1, "fpr": fpr,
                "tp": tp, "fp": fp, "fn": fn}

    return {
        "n_total": len(rows), "n_pos": n_pos, "n_neg": n_neg,
        "tier1": _stats(t1_tp, t1_fp, t1_fn, n_neg),
        "tier2": _stats(t2_tp, t2_fp, t2_fn, n_neg),
        "combined": _stats(combined_tp, combined_fp, combined_fn, n_neg),
    }


def _print_block(name: str, s: dict) -> None:
    print(f"  {name:8s} precision={s['precision']:.3f} "
          f"recall={s['recall']:.3f} f1={s['f1']:.3f} "
          f"fpr={s['fpr']:.3f}  (tp={s['tp']} fp={s['fp']} fn={s['fn']})")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", required=True,
                   help="Path to labeled JSONL file.")
    p.add_argument("--phrases", default=None,
                   help="JSON file of Tier 1 phrases. Falls back to "
                        "the seed list embedded in this script.")
    p.add_argument("--tokens", default=None,
                   help="JSON file of Tier 2 anaphoric tokens. "
                        "Falls back to the seed list.")
    p.add_argument("--target-recall", type=float, default=0.85)
    p.add_argument("--target-fpr", type=float, default=0.15)
    args = p.parse_args()

    if not Path(args.labels).exists():
        print(f"ERROR: labels file not found: {args.labels}", file=sys.stderr)
        return 3

    rows = []
    with open(args.labels) as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"ERROR: line {i}: {e}", file=sys.stderr)
                return 2
            if "label" not in row or "prompt" not in row:
                print(f"ERROR: line {i}: missing 'label' or 'prompt'",
                      file=sys.stderr)
                return 2
            if row["label"] not in ("yes", "no"):
                print(f"ERROR: line {i}: label must be yes/no",
                      file=sys.stderr)
                return 2
            rows.append(row)

    if not rows:
        print("ERROR: no rows in labels file", file=sys.stderr)
        return 2

    phrases = _load_json_or_seed(args.phrases, SEED_TIER1_PHRASES)
    tokens = _load_json_or_seed(args.tokens, SEED_TIER2_TOKENS)

    try:
        report = evaluate(rows, phrases, tokens)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"n_total={report['n_total']} n_pos={report['n_pos']} "
          f"n_neg={report['n_neg']}")
    _print_block("tier1", report["tier1"])
    _print_block("tier2", report["tier2"])
    _print_block("combined", report["combined"])

    c = report["combined"]
    pass_recall = c["recall"] >= args.target_recall
    pass_fpr = c["fpr"] <= args.target_fpr

    print()
    print(f"Target: combined recall >= {args.target_recall:.2f} "
          f"AND combined FPR <= {args.target_fpr:.2f}")
    print(f"  recall: {c['recall']:.3f} -> "
          f"{'PASS' if pass_recall else 'FAIL'}")
    print(f"  FPR:    {c['fpr']:.3f} -> "
          f"{'PASS' if pass_fpr else 'FAIL'}")

    return 0 if (pass_recall and pass_fpr) else 1


if __name__ == "__main__":
    sys.exit(main())
