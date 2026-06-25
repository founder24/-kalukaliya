"""
RAG Evaluation Harness (RAGAS-compatible)

Evaluates the Syrabit RAG pipeline offline using a golden test set.
No labels required — uses LLM-as-judge pattern via Sarvam AI.

Metrics:
  faithfulness      — does the answer stay within the retrieved chunks?
  answer_relevancy  — does the answer address the question?
  context_recall    — did retrieval find the relevant information?

Target scores: faithfulness > 0.85, answer_relevancy > 0.80

Usage:
    python -m app.services.rag.evaluator --dataset eval_set.json --output results.json

Dataset format (eval_set.json):
    [
      {
        "question": "What is wave optics?",
        "ground_truth": "Wave optics studies light as a wave...",
        "language": "en",
        "filters": { "subject_id": "s13", "source_type": "notes" }
      },
      ...
    ]
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EvalSample:
    question: str
    ground_truth: str
    language: str = "en"
    filters: dict = field(default_factory=dict)

    generated_answer: str = ""
    retrieved_contexts: list[str] = field(default_factory=list)
    retrieval_path: str = ""
    latency_ms: float = 0.0

    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_recall: float = 0.0


@dataclass
class EvalReport:
    total: int = 0
    faithfulness_mean: float = 0.0
    answer_relevancy_mean: float = 0.0
    context_recall_mean: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    pass_rate: float = 0.0
    samples: list[dict] = field(default_factory=list)


async def run_evaluation(
    dataset: list[dict],
    concurrency: int = 4,
) -> EvalReport:
    """
    Run the full RAG evaluation pipeline over a dataset.

    Args:
        dataset: List of dicts matching EvalSample fields.
        concurrency: Number of concurrent eval calls (default 4 to respect Sarvam rate limits).

    Returns:
        EvalReport with aggregate metrics and per-sample results.
    """
    samples = [EvalSample(**d) for d in dataset]
    semaphore = asyncio.Semaphore(concurrency)

    async def _eval_one(sample: EvalSample) -> EvalSample:
        async with semaphore:
            return await _evaluate_sample(sample)

    results = await asyncio.gather(
        *[_eval_one(s) for s in samples], return_exceptions=True
    )

    evaluated: list[EvalSample] = []
    for r in results:
        if isinstance(r, Exception):
            logger.error(f"Eval sample failed: {r}")
        else:
            evaluated.append(r)

    if not evaluated:
        return EvalReport()

    latencies = sorted(s.latency_ms for s in evaluated)
    n = len(evaluated)

    report = EvalReport(
        total=n,
        faithfulness_mean=sum(s.faithfulness for s in evaluated) / n,
        answer_relevancy_mean=sum(s.answer_relevancy for s in evaluated) / n,
        context_recall_mean=sum(s.context_recall for s in evaluated) / n,
        latency_p50_ms=latencies[n // 2],
        latency_p95_ms=latencies[int(n * 0.95)],
        pass_rate=sum(
            1
            for s in evaluated
            if s.faithfulness >= 0.80 and s.answer_relevancy >= 0.75
        )
        / n,
        samples=[asdict(s) for s in evaluated],
    )

    logger.info(
        f"RAG Eval complete: n={n} "
        f"faithfulness={report.faithfulness_mean:.3f} "
        f"relevancy={report.answer_relevancy_mean:.3f} "
        f"recall={report.context_recall_mean:.3f} "
        f"pass_rate={report.pass_rate:.1%}"
    )
    return report


async def _evaluate_sample(sample: EvalSample) -> EvalSample:
    """Run retrieval + generation + scoring for one eval sample."""
    from app.services.rag.retrieval import retrieve
    from app.services.ai.sarvam_client import generate_with_sarvam

    t0 = time.time()

    chunks, path = await retrieve(
        query=sample.question,
        lang=sample.language,
        filters=sample.filters,
    )
    sample.retrieved_contexts = [c["content"] for c in chunks]
    sample.retrieval_path = path

    if not sample.retrieved_contexts:
        sample.latency_ms = (time.time() - t0) * 1000
        return sample

    context_block = "\n\n".join(
        f"[{i+1}] {ctx}" for i, ctx in enumerate(sample.retrieved_contexts)
    )
    system_prompt = (
        "You are a helpful study assistant. Answer the question using ONLY "
        "the provided context. Be concise and accurate."
    )
    user_message = f"Context:\n{context_block}\n\nQuestion: {sample.question}"

    try:
        answer = await generate_with_sarvam(
            system_prompt=system_prompt,
            user_message=user_message,
            stream=False,
        )
        sample.generated_answer = answer or ""
    except Exception as e:
        logger.warning(f"Generation failed for eval sample: {e}")
        sample.latency_ms = (time.time() - t0) * 1000
        return sample

    sample.latency_ms = (time.time() - t0) * 1000

    scores = await asyncio.gather(
        _score_faithfulness(sample),
        _score_answer_relevancy(sample),
        _score_context_recall(sample),
        return_exceptions=True,
    )

    sample.faithfulness = scores[0] if isinstance(scores[0], float) else 0.0
    sample.answer_relevancy = scores[1] if isinstance(scores[1], float) else 0.0
    sample.context_recall = scores[2] if isinstance(scores[2], float) else 0.0

    return sample


async def _score_faithfulness(sample: EvalSample) -> float:
    """
    Faithfulness: fraction of answer claims supported by retrieved context.
    Uses Sarvam as LLM judge.
    Scale: 0.0 (hallucinated) → 1.0 (fully grounded).
    """
    if not sample.generated_answer or not sample.retrieved_contexts:
        return 0.0

    context = "\n".join(sample.retrieved_contexts[:3])
    prompt = (
        "You are an evaluator. Rate how faithfully the ANSWER is grounded in "
        "the CONTEXT on a scale from 0.0 to 1.0.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"ANSWER:\n{sample.generated_answer}\n\n"
        "Respond with ONLY a decimal number between 0.0 and 1.0."
    )
    return await _llm_score(prompt)


async def _score_answer_relevancy(sample: EvalSample) -> float:
    """
    Answer relevancy: does the answer address the question?
    Scale: 0.0 (off-topic) → 1.0 (fully on-topic).
    """
    if not sample.generated_answer:
        return 0.0

    prompt = (
        "Rate how well the ANSWER addresses the QUESTION on a scale 0.0 to 1.0.\n\n"
        f"QUESTION:\n{sample.question}\n\n"
        f"ANSWER:\n{sample.generated_answer}\n\n"
        "Respond with ONLY a decimal number between 0.0 and 1.0."
    )
    return await _llm_score(prompt)


async def _score_context_recall(sample: EvalSample) -> float:
    """
    Context recall: does the retrieved context contain the ground truth answer?
    Scale: 0.0 (context missed) → 1.0 (context covers ground truth fully).
    """
    if not sample.retrieved_contexts or not sample.ground_truth:
        return 0.0

    context = "\n".join(sample.retrieved_contexts[:3])
    prompt = (
        "Rate how well the CONTEXT covers the GROUND TRUTH on a scale 0.0 to 1.0.\n\n"
        f"GROUND TRUTH:\n{sample.ground_truth}\n\n"
        f"CONTEXT:\n{context}\n\n"
        "Respond with ONLY a decimal number between 0.0 and 1.0."
    )
    return await _llm_score(prompt)


async def _llm_score(prompt: str) -> float:
    """Call Sarvam as LLM judge and parse the 0.0–1.0 score."""
    import re
    from app.services.ai.sarvam_client import generate_with_sarvam

    try:
        raw = await generate_with_sarvam(
            system_prompt="You are a precise evaluator. Respond with only a decimal number.",
            user_message=prompt,
            stream=False,
        )
        match = re.search(r"\b([01](?:\.\d+)?)\b", raw or "")
        if match:
            return min(1.0, max(0.0, float(match.group(1))))
    except Exception as e:
        logger.debug(f"LLM score call failed: {e}")
    return 0.0


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Syrabit RAG Evaluator")
    parser.add_argument("--dataset", required=True, help="Path to eval_set.json")
    parser.add_argument("--output", default="rag_eval_results.json", help="Output file")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    with open(args.dataset) as f:
        dataset = json.load(f)

    report = asyncio.run(run_evaluation(dataset, concurrency=args.concurrency))

    with open(args.output, "w") as f:
        json.dump(asdict(report), f, indent=2)

    print(f"\nRAG Evaluation Results ({report.total} samples)")
    print(f"  Faithfulness:     {report.faithfulness_mean:.3f}  (target > 0.85)")
    print(f"  Answer Relevancy: {report.answer_relevancy_mean:.3f}  (target > 0.80)")
    print(f"  Context Recall:   {report.context_recall_mean:.3f}")
    print(f"  Pass Rate:        {report.pass_rate:.1%}")
    print(f"  Latency p50:      {report.latency_p50_ms:.0f}ms")
    print(f"  Latency p95:      {report.latency_p95_ms:.0f}ms")
    print(f"\nFull results written to: {args.output}")

    if report.faithfulness_mean < 0.85 or report.answer_relevancy_mean < 0.80:
        print("\nWARNING: Below target thresholds. Review retrieval quality before go-live.")
        sys.exit(1)
