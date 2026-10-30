export default function SubjectReadinessPanel({ readiness, className = '' }) {
  if (!readiness?.required) return null;
  const scores = readiness.scores || {};
  const dimensions = [
    ['SEO', scores.seo],
    ['GEO', scores.geo],
    ['AEO', scores.aeo],
    ['RAG index', readiness.rag_score],
  ];
  const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
  const improvements = Array.isArray(readiness.improvements) ? readiness.improvements : [];
  const failedChecks = Array.isArray(readiness.checks)
    ? readiness.checks.filter((check) => !check.passed)
    : [];
  const ragChecks = Array.isArray(readiness.rag_checks) ? readiness.rag_checks : [];
  const failedRagChecks = ragChecks.filter((check) => !check.passed);
  const passedCount = [
    ...(Array.isArray(readiness.checks) ? readiness.checks : []),
    ...ragChecks,
  ].filter((check) => check.passed).length;
  const totalChecks = (Array.isArray(readiness.checks) ? readiness.checks.length : 0) + ragChecks.length;

  return (
    <section
      className={`rounded-xl border border-violet-100 bg-violet-50/60 p-3 ${className}`}
      data-testid="subject-publish-readiness"
      aria-label="Bilingual subject publishing readiness"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-bold uppercase tracking-wide text-violet-800">Publish readiness</h4>
          <p className="mt-0.5 text-[10px] text-gray-600">
            Server-calculated from saved card, syllabus, and indexed-vector evidence. Every score must reach 100 to publish.
          </p>
        </div>
        <span className={`text-[10px] font-semibold ${readiness.can_publish ? 'text-emerald-700' : 'text-amber-700'}`}>
          {readiness.can_publish ? 'Publish gate passed · all 100' : 'Publish gate requires SEO, GEO, AEO, and RAG index at 100'}
        </span>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
        {dimensions.map(([label, score]) => (
          <div key={label} className="rounded-lg border border-violet-100 bg-white px-2 py-1.5 text-center">
            <div className="text-[9px] text-gray-500">{label}</div>
            <div className={`text-xs font-bold ${score === 100 ? 'text-emerald-700' : (score ?? 0) >= 90 ? 'text-amber-700' : 'text-violet-800'}`}>
              {score ?? 0}/100
            </div>
          </div>
        ))}
      </div>

      <p className="mt-2 text-[10px] text-gray-600">
        {passedCount}/{totalChecks} publishing and RAG-index checks passed
      </p>

      {failedChecks.length > 0 && (
        <details className="mt-2 text-[10px] text-gray-600" data-testid="readiness-blockers">
          <summary className="cursor-pointer font-medium text-violet-800">
            {failedChecks.length} required fix{failedChecks.length === 1 ? '' : 'es'} to reach the publish gate
          </summary>
          <ul className="mt-1 space-y-1.5">
            {failedChecks.map((check) => (
              <li key={check.id} className="rounded-md border border-amber-100 bg-white/80 p-2">
                <span className="font-semibold text-gray-700">{check.group.toUpperCase()}: {check.label}</span>
                {check.fix_hint && <span className="mt-0.5 block text-gray-500">{check.fix_hint}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {failedRagChecks.length > 0 && (
        <details className="mt-2 text-[10px] text-gray-600" data-testid="rag-readiness-blockers">
          <summary className="cursor-pointer font-medium text-violet-800">
            {failedRagChecks.length} RAG index check{failedRagChecks.length === 1 ? ' needs' : 's need'} attention
          </summary>
          <ul className="mt-1 space-y-1.5">
            {failedRagChecks.map((check) => (
              <li key={check.id} className="rounded-md border border-amber-100 bg-white/80 p-2">
                <span className="font-semibold text-gray-700">{check.label}</span>
                <span className="mt-0.5 block text-gray-500">
                  Indexed {check.indexed_chunks} of {check.expected_chunks} expected chunks.
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {failedChecks.length === 0 && blockers.length === 0 && improvements.length > 0 && (
        <details className="mt-2 text-[10px] text-gray-600" data-testid="readiness-improvements">
          <summary className="cursor-pointer font-medium text-violet-800">
            {improvements.length} improvement{improvements.length === 1 ? '' : 's'} to reach 100
          </summary>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {improvements.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
          </ul>
        </details>
      )}

      <div className="mt-3 border-t border-violet-100 pt-2">
        <div className="text-[10px] font-bold text-violet-800">
          Topic pages: {readiness.topic_pages?.ready ?? 0}/{readiness.topic_pages?.total ?? 0} complete
        </div>
        {(readiness.topic_pages?.topics ?? []).length > 0 ? (
          <div className="mt-1.5 space-y-1.5">
            {readiness.topic_pages.topics.slice(0, 3).map((topic) => (
              <div key={topic.topic_id} className="flex items-start justify-between gap-2 text-[9px] text-gray-600">
                <span className="min-w-0 truncate">{topic.title_en || topic.title_as || topic.topic_id}</span>
                <span className={`shrink-0 font-semibold ${topic.ready ? 'text-emerald-700' : 'text-amber-700'}`}>
                  {topic.scores.seo}/{topic.scores.geo}/{topic.scores.aeo}
                </span>
              </div>
            ))}
            {readiness.topic_pages.total > 3 && (
              <div className="text-[9px] text-gray-500">+{readiness.topic_pages.total - 3} more topic pages</div>
            )}
          </div>
        ) : (
          <p className="mt-1 text-[9px] leading-relaxed text-gray-500">
            Topic scores appear after approved bilingual topic notes and explicitly linked question solutions are entered.
          </p>
        )}
      </div>

      <p className="mt-2 text-[9px] leading-relaxed text-gray-500">
        These are internal completeness checks, not search-ranking or AI-citation guarantees. Script checks cannot verify translation quality, and source links are not independently verified.
        RAG index readiness checks exact chunk-map coverage and freshness. Retrieval quality is not scored until reviewer-approved benchmark cases are available.
      </p>
    </section>
  );
}
