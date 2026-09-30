import { describe, expect, it } from 'vitest';

import { scoreBenchmark } from './chat-rag-benchmark.mjs';

function makeBenchmark(count = 100) {
  const cases = [];
  const observations = [];
  for (let index = 0; index < count; index += 1) {
    const id = `case-${String(index + 1).padStart(3, '0')}`;
    const sourceId = `chapter-${index + 1}`;
    cases.push({
      id,
      question: `Synthetic benchmark question ${index + 1}`,
      language: index % 2 === 0 ? 'en' : 'as',
      expected_action: 'answer',
      expected_source_ids: [sourceId],
      required_facts: [`fact-${index + 1}`],
      gold_review: { curriculum_verified: true, language_verified: true },
    });
    observations.push({
      case_id: id,
      answer: `Synthetic answer grounded in ${sourceId}.`,
      retrieved_source_ids: [sourceId],
      source_card_source_ids: [sourceId],
      review: {
        response: {
          grounding: 5,
          relevance: 5,
          completeness: 5,
          curriculum_scope: 5,
          language_clarity: 5,
          behavior: 5,
          unsupported_major_claims: 0,
          wrong_curriculum_scope: false,
        },
        source_card: {
          passage_fidelity: 5,
          hierarchy_accuracy: 5,
          confidence_honesty: 5,
          navigation: 5,
          fabricated_source: false,
        },
      },
    });
  }
  return { cases, observations };
}

describe('local Chat RAG benchmark scorer', () => {
  it('certifies a fully reviewed perfect 100-case benchmark', () => {
    const report = scoreBenchmark(makeBenchmark());

    expect(report).toMatchObject({
      status: 'target_met',
      case_count: 100,
      reviewed_observation_count: 100,
      response_quality_score_10: 10,
      source_card_quality_score_10: 10,
      retrieval_recall_at_5: 1,
      source_card_source_precision: 1,
      source_card_source_recall: 1,
      critical_failure_count: 0,
      target_met: true,
    });
  });

  it('reports retrieval misses and critical grounding errors without certifying', () => {
    const benchmark = makeBenchmark();
    benchmark.observations[0].retrieved_source_ids = ['wrong-source'];
    benchmark.observations[0].source_card_source_ids = ['wrong-source'];
    benchmark.observations[0].review.response.unsupported_major_claims = 1;
    benchmark.observations[0].review.source_card.fabricated_source = true;

    const report = scoreBenchmark(benchmark);

    expect(report.status).toBe('complete_below_target');
    expect(report.retrieval_recall_at_5).toBe(0.99);
    expect(report.source_card_source_precision).toBe(0.99);
    expect(report.source_card_source_recall).toBe(0.99);
    expect(report.critical_failure_count).toBe(1);
    expect(report.critical_failure_case_ids).toEqual(['case-001']);
    expect(report.target_met).toBe(false);
  });

  it('does not claim a score when there are no reviewed gold cases', () => {
    const report = scoreBenchmark({ cases: [], observations: [] });

    expect(report.status).toBe('not_scored');
    expect(report.response_quality_score_10).toBeNull();
    expect(report.source_card_quality_score_10).toBeNull();
    expect(report.target_met).toBe(false);
  });

  it('keeps small, fully reviewed sets as pilots rather than certification', () => {
    const benchmark = makeBenchmark(10);
    const report = scoreBenchmark(benchmark);

    expect(report.status).toBe('pilot');
    expect(report.response_quality_score_10).toBe(10);
    expect(report.target_met).toBe(false);
  });

  it('rejects unreviewed gold cases and invalid human ratings', () => {
    const benchmark = makeBenchmark(1);
    benchmark.cases[0].gold_review.curriculum_verified = false;
    expect(() => scoreBenchmark(benchmark)).toThrow(/gold review/);

    benchmark.cases[0].gold_review.curriculum_verified = true;
    benchmark.observations[0].review.response.grounding = 6;
    expect(() => scoreBenchmark(benchmark)).toThrow(/number from 0 to 5/);
  });
});