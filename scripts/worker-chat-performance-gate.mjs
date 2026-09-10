export const PASS_RULE =
  'A strict majority of samples for each route must meet the target, and the median must be at or below the target.';

export function validateProbeEvents(name, events) {
  const sourceIndex = events.findIndex(event => event.event === 'source_card');
  const tokenIndex = events.findIndex(event =>
    typeof event.content === 'string' && event.content.length > 0 && !event.done);
  const doneIndex = events.findIndex(event => event.event === 'syrabit_done');
  if (!(sourceIndex === 0 && tokenIndex > sourceIndex && doneIndex > tokenIndex)) {
    throw new Error(`${name} SSE order invalid: source=${sourceIndex}, token=${tokenIndex}, done=${doneIndex}`);
  }

  const sourceCard = events[sourceIndex];
  const done = events[doneIndex];
  if (typeof done?.model !== 'string' || !done.model.startsWith('@cf/')) {
    throw new Error(`${name} did not report a native Workers AI model: ${done?.model}`);
  }
  return { sourceCard, done };
}

export function validateRouteResult(route, result) {
  if (route === 'direct' && result.rag_path !== 'chapter_direct') {
    throw new Error(`Direct RAG probe used unexpected path: ${result.rag_path}`);
  }
  if (route === 'web' && (result.web_used !== true || result.web_status !== 'ok')) {
    throw new Error(`Web probe did not return attributed web context: ${JSON.stringify(result)}`);
  }
}

export function summarizeRoute(results, targetMs) {
  const values = results.map(result => result.first_token_ms).sort((a, b) => a - b);
  const passingSamples = values.filter(value => value <= targetMs).length;
  const requiredPassingSamples = Math.floor(values.length / 2) + 1;
  const medianIndex = Math.floor(values.length / 2);
  const p95Index = Math.max(0, Math.ceil(values.length * 0.95) - 1);
  return {
    samples: values.length,
    passing_samples: passingSamples,
    required_passing_samples: requiredPassingSamples,
    first_token_median_ms: values[medianIndex],
    first_token_p95_ms: values[p95Index],
    first_token_max_ms: values.at(-1),
    passed: passingSamples >= requiredPassingSamples && values[medianIndex] <= targetMs,
  };
}

export function buildReport({ origin, targetMs, subject, chapter, directSamples, webSamples }) {
  const summary = {
    ...(directSamples.length > 0 && {
      direct_chapter_rag: summarizeRoute(directSamples, targetMs),
    }),
    ...(webSamples.length > 0 && {
      rag_plus_bounded_web: summarizeRoute(webSamples, targetMs),
    }),
  };
  return {
    origin,
    first_token_target_ms: targetMs,
    pass_rule: PASS_RULE,
    chapter: {
      id: chapter.chapter_id,
      title: chapter.title,
      subject: subject.name,
    },
    summary,
    probes: [...directSamples, ...webSamples],
  };
}

export function failedRouteMessages(summary, targetMs) {
  return Object.entries(summary)
    .filter(([, routeSummary]) => !routeSummary.passed)
    .map(([route, routeSummary]) =>
      `${route} (${routeSummary.passing_samples}/${routeSummary.samples} samples met ${targetMs} ms; `
      + `median ${routeSummary.first_token_median_ms} ms)`);
}