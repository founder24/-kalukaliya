#!/usr/bin/env node

import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_CASES = fileURLToPath(new URL('../evals/chat-rag/cases.json', import.meta.url));
const DEFAULT_OBSERVATIONS = fileURLToPath(
  new URL('../evals/chat-rag/observations.json', import.meta.url),
);
const CERTIFICATION_MINIMUM_CASES = 100;

const RESPONSE_WEIGHTS = {
  grounding: 0.30,
  relevance: 0.20,
  completeness: 0.15,
  curriculum_scope: 0.15,
  language_clarity: 0.10,
  behavior: 0.10,
};

const SOURCE_CARD_WEIGHTS = {
  source_precision: 0.20,
  source_recall: 0.20,
  passage_fidelity: 0.20,
  hierarchy_accuracy: 0.15,
  confidence_honesty: 0.15,
  navigation: 0.10,
};

const RESPONSE_REVIEW_FIELDS = Object.keys(RESPONSE_WEIGHTS);
const SOURCE_CARD_REVIEW_FIELDS = [
  'passage_fidelity',
  'hierarchy_accuracy',
  'confidence_honesty',
  'navigation',
];

function fail(message) {
  throw new Error(message);
}

function assertUniqueStrings(value, label) {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string' || !item.trim())) {
    fail(`${label} must be an array of non-empty strings`);
  }
  if (new Set(value).size !== value.length) {
    fail(`${label} must not contain duplicates`);
  }
}

function assertReview(review, fields, label) {
  if (!review || typeof review !== 'object' || Array.isArray(review)) {
    fail(`${label} is required`);
  }
  for (const field of fields) {
    const value = review[field];
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 5) {
      fail(`${label}.${field} must be a number from 0 to 5`);
    }
  }
}

function validateCases(cases) {
  if (!Array.isArray(cases)) fail('cases must be an array');
  const ids = new Set();

  for (const item of cases) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      fail('every case must be an object');
    }
    if (typeof item.id !== 'string' || !item.id.trim()) fail('every case needs a non-empty id');
    if (ids.has(item.id)) fail(`duplicate case id: ${item.id}`);
    ids.add(item.id);
    if (typeof item.question !== 'string' || !item.question.trim()) {
      fail(`case ${item.id} needs a question`);
    }
    if (!['en', 'as'].includes(item.language)) {
      fail(`case ${item.id} language must be "en" or "as"`);
    }
    if (!['answer', 'clarify', 'abstain'].includes(item.expected_action)) {
      fail(`case ${item.id} has an invalid expected_action`);
    }
    assertUniqueStrings(item.expected_source_ids, `case ${item.id} expected_source_ids`);
    assertUniqueStrings(item.required_facts, `case ${item.id} required_facts`);
    if (item.expected_action === 'answer'
      && (item.expected_source_ids.length === 0 || item.required_facts.length === 0)) {
      fail(`answer case ${item.id} needs expected_source_ids and required_facts`);
    }
    if (!item.gold_review
      || item.gold_review.curriculum_verified !== true
      || item.gold_review.language_verified !== true) {
      fail(`case ${item.id} needs curriculum and language gold review`);
    }
  }
  return ids;
}

function validateObservations(observations, caseIds) {
  if (!Array.isArray(observations)) fail('observations must be an array');
  const observedIds = new Set();

  for (const item of observations) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) {
      fail('every observation must be an object');
    }
    if (typeof item.case_id !== 'string' || !caseIds.has(item.case_id)) {
      fail(`observation references unknown case_id: ${item.case_id ?? '(missing)'}`);
    }
    if (observedIds.has(item.case_id)) fail(`duplicate observation for case ${item.case_id}`);
    observedIds.add(item.case_id);
    if (typeof item.answer !== 'string') fail(`observation ${item.case_id} needs answer text`);
    assertUniqueStrings(item.retrieved_source_ids, `observation ${item.case_id} retrieved_source_ids`);
    assertUniqueStrings(item.source_card_source_ids, `observation ${item.case_id} source_card_source_ids`);

    assertReview(item.review?.response, RESPONSE_REVIEW_FIELDS, `observation ${item.case_id} review.response`);
    assertReview(
      item.review?.source_card,
      SOURCE_CARD_REVIEW_FIELDS,
      `observation ${item.case_id} review.source_card`,
    );
    const answerReview = item.review.response;
    if (!Number.isInteger(answerReview.unsupported_major_claims)
      || answerReview.unsupported_major_claims < 0) {
      fail(`observation ${item.case_id} review.response.unsupported_major_claims must be a non-negative integer`);
    }
    if (typeof answerReview.wrong_curriculum_scope !== 'boolean') {
      fail(`observation ${item.case_id} review.response.wrong_curriculum_scope must be boolean`);
    }
    if (typeof item.review.source_card.fabricated_source !== 'boolean') {
      fail(`observation ${item.case_id} review.source_card.fabricated_source must be boolean`);
    }
  }
  return observedIds;
}

function average(values) {
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function round(value) {
  return value == null ? null : Math.round(value * 100) / 100;
}

function weightedRating(review, weights) {
  return Object.entries(weights)
    .reduce((sum, [key, weight]) => sum + (review[key] / 5) * weight, 0) * 10;
}

function sourceSetScores(expectedIds, actualIds) {
  const expected = new Set(expectedIds);
  const actual = new Set(actualIds);
  let overlap = 0;
  for (const id of expected) {
    if (actual.has(id)) overlap += 1;
  }

  if (expected.size === 0 && actual.size === 0) {
    return { precision: 1, recall: 1 };
  }
  return {
    precision: actual.size === 0 ? 0 : overlap / actual.size,
    recall: expected.size === 0 ? 0 : overlap / expected.size,
  };
}

/**
 * Score reviewed, locally captured observations against a reviewed gold set.
 * This function has no network, AI-provider, or database dependencies.
 */
export function scoreBenchmark({
  cases,
  observations,
  minimumCases = CERTIFICATION_MINIMUM_CASES,
}) {
  if (!Number.isInteger(minimumCases) || minimumCases < 1) {
    fail('minimumCases must be a positive integer');
  }
  const caseIds = validateCases(cases);
  const observedIds = validateObservations(observations, caseIds);
  const caseById = new Map(cases.map((item) => [item.id, item]));
  const observationById = new Map(observations.map((item) => [item.case_id, item]));
  const answerScores = [];
  const cardScores = [];
  const retrievalRecalls = [];
  const cardPrecisions = [];
  const cardRecalls = [];
  const responseDimensions = Object.fromEntries(RESPONSE_REVIEW_FIELDS.map((key) => [key, []]));
  const cardDimensions = Object.fromEntries(SOURCE_CARD_REVIEW_FIELDS.map((key) => [key, []]));
  const criticalFailureCaseIds = new Set();

  for (const observation of observations) {
    const testCase = caseById.get(observation.case_id);
    const responseReview = observation.review.response;
    const cardReview = observation.review.source_card;

    answerScores.push(weightedRating(responseReview, RESPONSE_WEIGHTS));
    for (const field of RESPONSE_REVIEW_FIELDS) {
      responseDimensions[field].push(responseReview[field]);
    }
    if (responseReview.unsupported_major_claims > 0
      || responseReview.wrong_curriculum_scope
      || cardReview.fabricated_source) {
      criticalFailureCaseIds.add(testCase.id);
    }

    const retrieval = sourceSetScores(
      testCase.expected_source_ids,
      observation.retrieved_source_ids.slice(0, 5),
    );
    const card = sourceSetScores(
      testCase.expected_source_ids,
      observation.source_card_source_ids,
    );
    retrievalRecalls.push(retrieval.recall);
    cardPrecisions.push(card.precision);
    cardRecalls.push(card.recall);
    for (const field of SOURCE_CARD_REVIEW_FIELDS) {
      cardDimensions[field].push(cardReview[field]);
    }
    const cardReviewAsFractions = {
      source_precision: card.precision,
      source_recall: card.recall,
      passage_fidelity: cardReview.passage_fidelity / 5,
      hierarchy_accuracy: cardReview.hierarchy_accuracy / 5,
      confidence_honesty: cardReview.confidence_honesty / 5,
      navigation: cardReview.navigation / 5,
    };
    const cardScore = Object.entries(SOURCE_CARD_WEIGHTS)
      .reduce((sum, [key, weight]) => sum + cardReviewAsFractions[key] * weight, 0) * 10;
    cardScores.push(cardScore);
  }

  const caseCount = cases.length;
  const observationCount = observedIds.size;
  const responseScore = average(answerScores);
  const sourceCardScore = average(cardScores);
  const retrievalRecallAt5 = average(retrievalRecalls);
  const sourceCardPrecision = average(cardPrecisions);
  const sourceCardRecall = average(cardRecalls);
  const targetMet = caseCount >= minimumCases
    && observationCount === caseCount
    && round(responseScore) === 10
    && round(sourceCardScore) === 10
    && round(retrievalRecallAt5) === 1
    && round(sourceCardPrecision) === 1
    && round(sourceCardRecall) === 1
    && criticalFailureCaseIds.size === 0;

  let status = 'complete_below_target';
  if (caseCount === 0) status = 'not_scored';
  else if (observationCount < caseCount) status = 'incomplete';
  else if (caseCount < minimumCases) status = 'pilot';
  else if (targetMet) status = 'target_met';

  return {
    benchmark: 'chat-rag',
    status,
    certification_minimum_cases: minimumCases,
    case_count: caseCount,
    reviewed_observation_count: observationCount,
    review_coverage: caseCount === 0 ? 0 : round(observationCount / caseCount),
    response_quality_score_10: round(responseScore),
    source_card_quality_score_10: round(sourceCardScore),
    retrieval_recall_at_5: round(retrievalRecallAt5),
    source_card_source_precision: round(sourceCardPrecision),
    source_card_source_recall: round(sourceCardRecall),
    response_review_averages_0_to_5: Object.fromEntries(
      Object.entries(responseDimensions).map(([key, values]) => [key, round(average(values))]),
    ),
    source_card_review_averages_0_to_5: Object.fromEntries(
      Object.entries(cardDimensions).map(([key, values]) => [key, round(average(values))]),
    ),
    critical_failure_count: criticalFailureCaseIds.size,
    critical_failure_case_ids: [...criticalFailureCaseIds].sort(),
    target_met: targetMet,
  };
}

function parseArgs(args) {
  const options = {
    cases: DEFAULT_CASES,
    observations: DEFAULT_OBSERVATIONS,
    output: null,
    help: false,
  };
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--') {
      continue;
    } else if (arg === '--help' || arg === '-h') {
      options.help = true;
    } else if (['--cases', '--observations', '--out'].includes(arg)) {
      const value = args[index + 1];
      if (!value || value.startsWith('--')) fail(`${arg} requires a path`);
      const optionName = arg === '--out' ? 'output' : arg.slice(2);
      options[optionName] = resolve(value);
      index += 1;
    } else {
      fail(`unknown option: ${arg}`);
    }
  }
  return options;
}

async function readDocument(path, arrayKey) {
  let text;
  try {
    text = await readFile(path, 'utf8');
  } catch (error) {
    fail(`cannot read ${path}: ${error.message}`);
  }
  let document;
  try {
    document = JSON.parse(text);
  } catch (error) {
    fail(`invalid JSON in ${path}: ${error.message}`);
  }
  if (!document || !Array.isArray(document[arrayKey])) {
    fail(`${path} must contain an array named "${arrayKey}"`);
  }
  return document[arrayKey];
}

const HELP = `Local Chat RAG benchmark scorer (offline; makes no HTTP or AI-provider calls)

Usage:
  pnpm --filter syrabit-api eval:chat-rag
  pnpm --filter syrabit-api eval:chat-rag -- --cases path/to/cases.json --observations path/to/observations.json --out report.json

The default benchmark files are apps/api/evals/chat-rag/cases.json and observations.json.
Certification requires at least ${CERTIFICATION_MINIMUM_CASES} curriculum-reviewed cases.
`;

async function main(args = process.argv.slice(2)) {
  const options = parseArgs(args);
  if (options.help) {
    console.log(HELP);
    return;
  }
  const [cases, observations] = await Promise.all([
    readDocument(options.cases, 'cases'),
    readDocument(options.observations, 'observations'),
  ]);
  const report = scoreBenchmark({ cases, observations });
  const json = `${JSON.stringify(report, null, 2)}\n`;
  if (options.output) await writeFile(options.output, json, 'utf8');
  console.log(json.trimEnd());
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`chat-rag-benchmark: ${error.message}`);
    process.exitCode = 1;
  });
}