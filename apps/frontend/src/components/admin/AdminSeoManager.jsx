import AdminQuickLinks from './AdminQuickLinks';
import { Loader2, RefreshCw, Play, BookOpen, CheckCircle2, FileText, Globe, Activity } from 'lucide-react';
import StatCard from './seo-manager/StatCard';
import JobProgress from './seo-manager/JobProgress';
import ReviewTab from './seo-manager/ReviewTab';
import PagesTab from './seo-manager/PagesTab';
import TopicsTab from './seo-manager/TopicsTab';
import InsightsTab from './seo-manager/InsightsTab';
import GenerateTab from './seo-manager/GenerateTab';
import PilotTab from './seo-manager/PilotTab';
import LinksTab from './seo-manager/LinksTab';
import SchemaTab from './seo-manager/SchemaTab';
import SitemapTab from './seo-manager/SitemapTab';
import PipelineTab from './seo-manager/PipelineTab';
import QualityTab from './seo-manager/QualityTab';
import SchedulePanel from './seo-manager/SchedulePanel';
import TopicDiscoveryTab from './seo-manager/TopicDiscoveryTab';
import RemediationTab from './seo-manager/RemediationTab';
import EntitySeoTab from './seo-manager/EntitySeoTab';
import useSeoManager from './seo-manager/useSeoManager';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
export default function AdminSeoManager({ adminToken, onNavigate }) {
  const s = useSeoManager(adminToken);

  const TABS = [
    { id: 'pipeline', label: '⚡ Pipeline', count: s.subjectCoverage.length || null },
    { id: 'review',   label: '🔍 Review',  count: s.reviewQueue.length || null },
    { id: 'pages',    label: 'SEO Pages',  count: s.pages.length },
    { id: 'topics',   label: 'Topics',     count: s.topics.length },
    { id: 'insights', label: '✦ Insights', count: s.insights?.insights?.length ?? null },
    { id: 'generate', label: 'Generate',   count: null },
    { id: 'pilot',    label: 'Pilot',      count: null },
    { id: 'links',    label: '🔗 Int. Links', count: null },
    { id: 'schema',   label: '🧬 Schema',  count: null },
    { id: 'sitemap',  label: '🗺 Sitemap', count: null },
    { id: 'quality',  label: '🛡 Quality', count: null },
    { id: 'schedule', label: '🗓 Schedule', count: null },
    { id: 'discovery', label: '🤖 Discovery', count: null },
    { id: 'remediation', label: '♻️ Remediation', count: null },
    { id: 'entity', label: '🪪 Entity SEO', count: null },
  ];

  return (
    <SectionErrorBoundary name="SEO Manager">
      <div className="space-y-5 max-w-5xl">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-lg font-bold text-gray-900">SEO Content Manager</h2>
            <p className="text-sm mt-0.5 text-gray-400">Manage topic pages, generate AI content, and control what Googlebot crawls</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => s.load()} disabled={s.loading}
              className="p-1.5 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors text-gray-400">
              <RefreshCw size={14} className={s.loading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={() => s.handleAutoRun()}
              disabled={s.activeJob?.status === 'queued' || s.activeJob?.status === 'running'}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-violet-50 border border-violet-200 hover:bg-violet-100 transition-colors text-violet-600">
              {s.activeJob?.status === 'running' ? <Loader2 size={13} className="animate-spin" />
                : <><Play size={13} /> Auto-Run All</>}
            </button>
          </div>
        </div>

        {s.activeJob && (
          <JobProgress job={s.activeJob} onDismiss={() => s.setActiveJob(null)} />
        )}

        {s.loading ? (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="rounded-xl p-4 border border-gray-200 h-24 animate-pulse bg-gray-50" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            <StatCard icon={BookOpen}     label="Topics"          value={s.topics.length}      color="#374151" />
            <StatCard icon={CheckCircle2} label="Published"       value={s.publishedCount}     color="#10b981" />
            <StatCard icon={FileText}     label="Drafts"          value={s.draftCount}         color="#f59e0b" />
            <StatCard icon={Globe}        label="Sitemap URLs"    value={s.stats?.sitemap_urls ?? s.publishedCount} color="#7c3aed" />
            <StatCard icon={Activity}     label="Coverage"        value={`${s.coverage}%`}     color={s.coverage >= 80 ? '#10b981' : s.coverage >= 40 ? '#f59e0b' : '#ef4444'}
              sub={`${s.topics.length} topics × 5 types`} />
          </div>
        )}

        <div className="flex gap-1 p-1 rounded-xl overflow-x-auto bg-gray-100">
          {TABS.map(({ id, label, count }) => (
            <button key={id} onClick={() => s.setTab(id)}
              className={`flex-shrink-0 h-8 px-3 rounded-lg text-xs font-semibold transition-all flex items-center gap-1.5 ${
                s.tab === id ? 'text-white shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}
              style={s.tab === id ? { background: '#7c3aed', color: '#fff' } : {}}>
              {label}
              {count !== null && (
                <span className="px-1.5 py-0.5 rounded-full text-[10px]"
                  style={{ background: s.tab === id ? 'rgba(124,58,237,0.15)' : '#e5e7eb', color: s.tab === id ? '#7c3aed' : '#9ca3af' }}>
                  {count}
                </span>
              )}
            </button>
          ))}
        </div>

        {s.tab === 'review' && (
          <ReviewTab adminToken={adminToken} reviewQueue={s.reviewQueue} setReviewQueue={s.setReviewQueue}
            reviewLoading={s.reviewLoading} reviewSelected={s.reviewSelected} setReviewSelected={s.setReviewSelected}
            flagging={s.flagging} setFlagging={s.setFlagging} bulkThreshold={s.bulkThreshold}
            setBulkThreshold={s.setBulkThreshold} loadReviewQueue={s.loadReviewQueue} />
        )}

        {s.tab === 'pages' && (
          <PagesTab loading={s.loading} filteredPages={s.filteredPages} pages={s.pages}
            publishedCount={s.publishedCount} draftCount={s.draftCount}
            pageSearch={s.pageSearch} setPageSearch={s.setPageSearch}
            pageFilter={s.pageFilter} setPageFilter={s.setPageFilter}
            pageSort={s.pageSort} setPageSort={s.setPageSort}
            minCombined={s.minCombined} setMinCombined={s.setMinCombined}
            handleToggleStatus={s.handleToggleStatus} handleAutoRun={s.handleAutoRun} />
        )}

        {s.tab === 'topics' && (
          <TopicsTab loading={s.loading} filteredTopics={s.filteredTopics} topics={s.topics}
            topicSearch={s.topicSearch} setTopicSearch={s.setTopicSearch}
            selectedTopics={s.selectedTopics} toggleTopic={s.toggleTopic}
            extracting={s.extracting} handleExtract={s.handleExtract}
            handleDeleteTopic={s.handleDeleteTopic} hubCtx={s.hubCtx}
            scopeSubjectOnly={s.scopeSubjectOnly} setScopeSubjectOnly={s.setScopeSubjectOnly}
            onNavigate={onNavigate} setTab={s.setTab} />
        )}

        {s.tab === 'insights' && (
          <InsightsTab insights={s.insights} insightsLoading={s.insightsLoading}
            loadInsights={s.loadInsights} handleInsightAction={s.handleInsightAction}
            actionLoading={s.actionLoading} />
        )}

        {s.tab === 'generate' && (
          <GenerateTab selectedTopics={s.selectedTopics} selectedTypes={s.selectedTypes}
            topics={s.topics} generating={s.generating} toggleTopic={s.toggleTopic}
            toggleType={s.toggleType} handleGenerate={s.handleGenerate} setTab={s.setTab} />
        )}

        {s.tab === 'pilot' && (
          <PilotTab piloting={s.piloting} pilotResult={s.pilotResult}
            pilotBoard={s.pilotBoard} setPilotBoard={s.setPilotBoard}
            pilotClass={s.pilotClass} setPilotClass={s.setPilotClass}
            pilotSubject={s.pilotSubject} setPilotSubject={s.setPilotSubject}
            pilotChapters={s.pilotChapters} setPilotChapters={s.setPilotChapters}
            handlePilot={s.handlePilot} />
        )}

        {s.tab === 'links' && (
          <LinksTab adminToken={adminToken}
            linksData={s.linksData} linksLoading={s.linksLoading}
            handleLinksAnalyze={s.handleLinksAnalyze} injectSlug={s.injectSlug}
            setInjectSlug={s.setInjectSlug} injecting={s.injecting}
            handleLinksInject={s.handleLinksInject} />
        )}

        {s.tab === 'schema' && (
          <SchemaTab schemaSlug={s.schemaSlug} setSchemaSlug={s.setSchemaSlug}
            schemaLoading={s.schemaLoading} schemaResult={s.schemaResult}
            handleSchemaInjectSingle={s.handleSchemaInjectSingle}
            handleSchemaBulk={s.handleSchemaBulk} publishedCount={s.publishedCount} />
        )}

        {s.tab === 'sitemap' && (
          <SitemapTab sitemapData={s.sitemapData} sitemapValidating={s.sitemapValidating}
            handleSitemapValidate={s.handleSitemapValidate} refreshingMeta={s.refreshingMeta}
            handleRefreshMeta={s.handleRefreshMeta} sitemap={s.sitemap}
            handleRegenerateSitemap={s.handleRegenerateSitemap}
            adminToken={adminToken} />
        )}

        {s.tab === 'pipeline' && (
          <PipelineTab subjectCoverage={s.subjectCoverage} coverageLoading={s.coverageLoading}
            loadCoverage={s.loadCoverage} subjectJobs={s.subjectJobs}
            handleRunSubject={s.handleRunSubject} handleAutoRun={s.handleAutoRun}
            activeJob={s.activeJob} setActiveJob={s.setActiveJob}
            pipelineSearch={s.pipelineSearch} setPipelineSearch={s.setPipelineSearch}
            handleBackfillNotes={s.handleBackfillNotes} backfilling={s.backfilling}
            handleDiagnoseTopics={s.handleDiagnoseTopics}
            diagnostics={s.diagnostics} diagnosticsLoading={s.diagnosticsLoading} />
        )}

        {s.tab === 'quality' && (
          <QualityTab adminToken={adminToken} />
        )}

        {s.tab === 'schedule' && (
          <SchedulePanel schedule={s.schedule} scheduleLoading={s.scheduleLoading}
            loadSchedule={s.loadSchedule} />
        )}

        {s.tab === 'discovery' && (
          <TopicDiscoveryTab adminToken={adminToken} />
        )}

        {s.tab === 'remediation' && (
          <RemediationTab adminToken={adminToken} />
        )}

        {s.tab === 'entity' && (
          <EntitySeoTab adminToken={adminToken} />
        )}

        <AdminQuickLinks links={['content','vertex','analytics','dashboard','editor']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
