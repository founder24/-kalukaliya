import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import {
  adminSeoStats, adminSeoListTopics, adminSeoExtractTopics,
  adminSeoGenerate, adminSeoListPages, adminSeoUpdatePageStatus,
  adminSeoRegenerateSitemap, adminSeoDeleteTopic, adminSeoPilot,
  adminSeoAutoRun, adminSeoJobStatus, adminSeoInsights,
  adminSeoBulkPublish, adminSeoSubjectCoverage, adminSeoRunSubject,
  seoInternalLinksAnalyze, seoInternalLinksInject,
  seoInjectSchemaBulk, seoInjectSchema, seoSitemapValidate,
  adminSeoRefreshMeta, adminSeoReviewQueue,
  adminSeoDiagnoseTopics, adminSeoBackfillNotes,
  adminSeoAutoPublishSchedule,
} from '@/utils/api';

const HUB_CTX_KEY = 'syrabit_hub_ctx';
function readHubCtx() {
  try {
    const raw = localStorage.getItem(HUB_CTX_KEY);
    if (!raw) return null;
    const ctx = JSON.parse(raw);
    if (Date.now() - (ctx._ts || 0) > 2 * 60 * 60 * 1000) return null;
    return ctx;
  } catch { return null; }
}

export default function useSeoManager(adminToken) {
  const [tab, setTab]               = useState('pages');
  const [stats, setStats]           = useState(null);
  const [topics, setTopics]         = useState([]);
  const [pages, setPages]           = useState([]);
  const [insights, setInsights]     = useState(null);
  const [loading, setLoading]       = useState(true);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [sitemap, setSitemap]       = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [activeJob, setActiveJob]   = useState(null);
  const [actionLoading, setActionLoading] = useState(null);
  const pollRef = useRef(null);

  const [topicSearch, setTopicSearch]   = useState('');
  const [pageSearch, setPageSearch]     = useState('');
  const [pageFilter, setPageFilter]     = useState('all');
  const [pageSort, setPageSort]         = useState('combined');
  const [minCombined, setMinCombined]   = useState(0);
  const [selectedTopics, setSelectedTopics]   = useState(new Set());
  const [selectedTypes, setSelectedTypes]     = useState(new Set(['notes', 'important-questions', 'mcqs']));

  const [piloting, setPiloting]     = useState(false);
  const [pilotResult, setPilotResult] = useState(null);
  const [pilotBoard, setPilotBoard]   = useState('AHSEC');
  const [pilotClass, setPilotClass]   = useState('Class 11');
  const [pilotSubject, setPilotSubject] = useState('');
  const [pilotChapters, setPilotChapters] = useState(3);

  const [hubCtx, setHubCtx] = useState(readHubCtx);
  const prevHubSubjectId = useRef('');
  useEffect(() => {
    const onFocus = () => setHubCtx(readHubCtx());
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, []);
  const [scopeSubjectOnly, setScopeSubjectOnly] = useState(false);

  useEffect(() => {
    if (hubCtx?.subjectId && hubCtx.subjectId !== prevHubSubjectId.current) {
      prevHubSubjectId.current = hubCtx.subjectId;
      setScopeSubjectOnly(true);
    }
  }, [hubCtx?.subjectId]);

  const [subjectCoverage, setSubjectCoverage] = useState([]);
  const [coverageLoading, setCoverageLoading] = useState(false);
  const [subjectJobs, setSubjectJobs]         = useState({});
  const [pipelineSearch, setPipelineSearch]   = useState('');
  const subjectPollsRef                       = useRef({});

  const loadCoverage = useCallback(async () => {
    setCoverageLoading(true);
    try {
      const res = await adminSeoSubjectCoverage(adminToken);
      setSubjectCoverage(res.data?.subjects || []);
    } catch { toast.error('Failed to load subject coverage'); }
    finally { setCoverageLoading(false); }
  }, [adminToken]);

  useEffect(() => {
    if (tab === 'pipeline' && subjectCoverage.length === 0) loadCoverage();
  }, [tab, subjectCoverage.length, loadCoverage]);

  const [reviewQueue, setReviewQueue] = useState([]);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewSelected, setReviewSelected] = useState(new Set());
  const [flagging, setFlagging] = useState(false);
  const [bulkThreshold, setBulkThreshold] = useState(70);

  const loadReviewQueue = useCallback(async () => {
    setReviewLoading(true);
    try {
      const res = await adminSeoReviewQueue(adminToken);
      setReviewQueue(res.data?.pages || []);
    } catch { toast.error('Failed to load review queue'); }
    finally { setReviewLoading(false); }
  }, [adminToken]);

  useEffect(() => {
    if (tab === 'review' && reviewQueue.length === 0) loadReviewQueue();
  }, [tab, reviewQueue.length, loadReviewQueue]);

  const [schedule, setSchedule] = useState(null);
  const [scheduleLoading, setScheduleLoading] = useState(false);

  const loadSchedule = useCallback(async () => {
    setScheduleLoading(true);
    try {
      const res = await adminSeoAutoPublishSchedule(adminToken);
      setSchedule(res.data || { config: null, last_marker: null, recent_runs: [] });
    } catch { toast.error('Failed to load auto-publish schedule'); }
    finally { setScheduleLoading(false); }
  }, [adminToken]);

  useEffect(() => {
    if (tab === 'schedule' && !schedule) loadSchedule();
  }, [tab, schedule, loadSchedule]);

  useEffect(() => () => Object.values(subjectPollsRef.current).forEach(clearInterval), []);

  const [linksData, setLinksData]     = useState(null);
  const [linksLoading, setLinksLoading] = useState(false);
  const [injectSlug, setInjectSlug]   = useState('');
  const [injecting, setInjecting]     = useState(false);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaSlug, setSchemaSlug]   = useState('');
  const [schemaResult, setSchemaResult] = useState(null);
  const [sitemapData, setSitemapData] = useState(null);
  const [sitemapValidating, setSitemapValidating] = useState(false);
  const [refreshingMeta, setRefreshingMeta] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, topicsRes, pagesRes] = await Promise.all([
        adminSeoStats(adminToken),
        adminSeoListTopics(adminToken),
        adminSeoListPages(adminToken),
      ]);
      setStats(statsRes.data);
      setTopics(Array.isArray(topicsRes.data) ? topicsRes.data : []);
      const raw = pagesRes.data;
      setPages(Array.isArray(raw) ? raw : (raw?.pages || []));
    } catch {
      toast.error('Failed to load SEO data');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  const loadInsights = useCallback(async () => {
    setInsightsLoading(true);
    try {
      const res = await adminSeoInsights(adminToken);
      setInsights(res.data);
    } catch {
      toast.error('Could not load insights');
    } finally {
      setInsightsLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (tab === 'insights' && !insights) loadInsights();
  }, [tab, insights, loadInsights]);

  const startPolling = useCallback((jobId) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await adminSeoJobStatus(adminToken, jobId);
        const job = res.data;
        setActiveJob(job);
        if (job.status === 'done' || job.status === 'error') {
          clearInterval(pollRef.current);
          pollRef.current = null;
          load();
          if (insights) loadInsights();
        }
      } catch {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 2000);
  }, [adminToken, load, insights, loadInsights]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const pollSubjectJob = useCallback((subjectId, jobId) => {
    if (subjectPollsRef.current[subjectId]) clearInterval(subjectPollsRef.current[subjectId]);
    subjectPollsRef.current[subjectId] = setInterval(async () => {
      try {
        const res = await adminSeoJobStatus(adminToken, jobId);
        const job = res.data;
        setSubjectJobs(prev => ({ ...prev, [subjectId]: job }));
        if (job.status === 'done' || job.status === 'error') {
          clearInterval(subjectPollsRef.current[subjectId]);
          delete subjectPollsRef.current[subjectId];
          loadCoverage();
          load();
        }
      } catch {
        clearInterval(subjectPollsRef.current[subjectId]);
        delete subjectPollsRef.current[subjectId];
      }
    }, 2500);
  }, [adminToken, loadCoverage, load]);

  const handleRunSubject = useCallback(async (subjectId, subjectName, force = false) => {
    try {
      setSubjectJobs(prev => ({ ...prev, [subjectId]: { status: 'queued', current: 'Queuing…', done: 0, total: 0 } }));
      const res = await adminSeoRunSubject(adminToken, subjectId, force);
      const jobId = res.data?.job_id;
      if (!jobId) throw new Error('No job_id returned');
      setSubjectJobs(prev => ({ ...prev, [subjectId]: { ...res.data, status: 'queued', current: 'Starting…' } }));
      pollSubjectJob(subjectId, jobId);
      toast.success(`Pipeline started for ${subjectName}`);
    } catch (err) {
      toast.error('Failed to start pipeline: ' + (err.response?.data?.detail || err.message));
      setSubjectJobs(prev => { const n = { ...prev }; delete n[subjectId]; return n; });
    }
  }, [adminToken, pollSubjectJob]);

  // ── Task #457: diagnostics + notes backfill ────────────────────────────
  const [diagnostics, setDiagnostics] = useState(null);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);
  const [backfilling, setBackfilling] = useState(false);

  const handleDiagnoseTopics = useCallback(async () => {
    setDiagnosticsLoading(true);
    try {
      const res = await adminSeoDiagnoseTopics(adminToken, { limit: 100, only_blocked: true });
      setDiagnostics(res.data || { items: [], summary: {} });
      const blocked = res.data?.summary?.blocked ?? 0;
      const ready = res.data?.summary?.ready ?? 0;
      toast.success(`Diagnostic: ${ready} ready · ${blocked} blocked`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Diagnostic failed');
    } finally {
      setDiagnosticsLoading(false);
    }
  }, [adminToken]);

  const handleBackfillNotes = useCallback(async () => {
    if (!confirm('Generate notes for every eligible topic that does not yet have one?')) return;
    setBackfilling(true);
    try {
      const res = await adminSeoBackfillNotes(adminToken);
      const jobId = res.data?.job_id;
      if (!jobId) throw new Error('No job_id returned');
      setActiveJob({ job_id: jobId, status: 'queued', total: 0, done: 0, errors: 0, skipped: 0, current: 'Backfill starting…', kind: 'backfill-notes' });
      startPolling(jobId);
      toast.success('Notes backfill started');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Backfill failed');
    } finally {
      setBackfilling(false);
    }
  }, [adminToken, startPolling]);

  const handleAutoRun = useCallback(async () => {
    try {
      toast.loading('Starting full pipeline…', { id: 'autorun' });
      const res = await adminSeoAutoRun(adminToken);
      const jobId = res.data?.job_id;
      if (!jobId) throw new Error('No job_id returned');
      setActiveJob({ job_id: jobId, status: 'queued', total: 0, done: 0, errors: 0, skipped: 0, current: 'Starting…' });
      startPolling(jobId);
      toast.success('Pipeline launched — tracking progress below', { id: 'autorun' });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Auto-run failed', { id: 'autorun' });
    }
  }, [adminToken, startPolling]);

  const handleExtract = useCallback(async (force = false) => {
    const sid = hubCtx?.subjectId || null;
    const label = sid && hubCtx?.subjectName ? ` for "${hubCtx.subjectName}"` : '';
    setExtracting(true);
    toast.loading(`Extracting topics${label} using AI…`, { id: 'extract' });
    try {
      const res = await adminSeoExtractTopics(adminToken, sid, force);
      const d = res.data || {};
      toast.success(
        `Created ${d.created || 0} topics${label}` +
        (d.skipped ? ` · ${d.skipped} already existed` : '') +
        (d.errors   ? ` · ${d.errors} AI errors` : ''),
        { id: 'extract' }
      );
      load();
    } catch {
      toast.error('Topic extraction failed', { id: 'extract' });
    } finally {
      setExtracting(false);
    }
  }, [adminToken, hubCtx, load]);

  const handleGenerate = useCallback(async () => {
    if (!selectedTopics.size) { toast.error('Select at least one topic'); return; }
    if (!selectedTypes.size)  { toast.error('Select at least one page type'); return; }
    setGenerating(true);
    try {
      const res = await adminSeoGenerate(adminToken, { topic_ids: [...selectedTopics], page_types: [...selectedTypes] });
      toast.success(`Generating ${res.data?.total || 0} pages in background…`);
      setTimeout(load, 3000);
    } catch {
      toast.error('Generation failed');
    } finally {
      setGenerating(false);
    }
  }, [adminToken, selectedTopics, selectedTypes, load]);

  const handleToggleStatus = useCallback(async (page) => {
    const newStatus = page.status === 'published' ? 'draft' : 'published';
    try {
      await adminSeoUpdatePageStatus(adminToken, page._id || page.id, newStatus);
      setPages(prev => prev.map(p => (p._id === page._id || p.id === page.id) ? { ...p, status: newStatus } : p));
      toast.success(`Page ${newStatus === 'published' ? 'published' : 'unpublished'}`);
    } catch { toast.error('Status update failed'); }
  }, [adminToken]);

  const handleDeleteTopic = useCallback(async (topic) => {
    if (!confirm(`Delete topic "${topic.title}"?`)) return;
    try {
      await adminSeoDeleteTopic(adminToken, topic._id || topic.id);
      setTopics(prev => prev.filter(t => (t._id || t.id) !== (topic._id || topic.id)));
      toast.success('Topic deleted');
    } catch { toast.error('Delete failed'); }
  }, [adminToken]);

  const handleRegenerateSitemap = useCallback(async () => {
    setSitemap(true);
    try {
      await adminSeoRegenerateSitemap(adminToken);
      toast.success('Sitemap regenerated');
    } catch { toast.error('Sitemap regeneration failed'); }
    finally { setSitemap(false); }
  }, [adminToken]);

  const handleBulkPublish = useCallback(async () => {
    if (!confirm(`Publish all draft SEO pages? This will make them publicly indexed.`)) return;
    setPublishing(true);
    try {
      const res = await adminSeoBulkPublish(adminToken);
      toast.success(res.data?.message || 'Pages published');
      load();
    } catch { toast.error('Bulk publish failed'); }
    finally { setPublishing(false); }
  }, [adminToken, load]);

  const handleInsightAction = useCallback(async (insight) => {
    setActionLoading(insight.title);
    try {
      if (insight.action === 'auto-run') {
        await handleAutoRun();
      } else if (insight.action === 'generate' && insight.page_type) {
        const res = await adminSeoGenerate(adminToken, {
          page_types: [insight.page_type],
          topic_ids: topics.slice(0, 200).map(t => t._id || t.id),
        });
        toast.success(`Generating ${res.data?.total || 0} ${insight.page_type} pages…`);
        setTimeout(load, 3000);
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Action failed');
    } finally {
      setActionLoading(null);
    }
  }, [adminToken, handleAutoRun, topics, load]);

  const handleLinksAnalyze = useCallback(async () => {
    setLinksLoading(true);
    try {
      const res = await seoInternalLinksAnalyze(adminToken);
      setLinksData(res.data);
    } catch { toast.error('Link analysis failed'); }
    finally { setLinksLoading(false); }
  }, [adminToken]);

  const handleLinksInject = useCallback(async () => {
    if (!injectSlug.trim()) { toast.error('Enter a slug'); return; }
    setInjecting(true);
    try {
      const res = await seoInternalLinksInject(adminToken, injectSlug.trim());
      toast.success(res.data?.message || 'Links injected');
    } catch (e) { toast.error(e.response?.data?.detail || 'Injection failed'); }
    finally { setInjecting(false); }
  }, [adminToken, injectSlug]);

  const handleSchemaInjectSingle = useCallback(async () => {
    if (!schemaSlug.trim()) { toast.error('Enter a slug'); return; }
    setSchemaLoading(true);
    try {
      const res = await seoInjectSchema(adminToken, schemaSlug.trim());
      setSchemaResult(res.data);
      toast.success('Schema injected');
    } catch (e) { toast.error(e.response?.data?.detail || 'Schema inject failed'); }
    finally { setSchemaLoading(false); }
  }, [adminToken, schemaSlug]);

  const handleSchemaBulk = useCallback(async () => {
    if (!confirm('Inject schema.org markup for ALL published pages? This will take a while.')) return;
    setSchemaLoading(true);
    try {
      const res = await seoInjectSchemaBulk(adminToken);
      toast.success(res.data?.message || 'Bulk schema injection started');
    } catch (e) { toast.error(e.response?.data?.detail || 'Bulk inject failed'); }
    finally { setSchemaLoading(false); }
  }, [adminToken]);

  const handleSitemapValidate = useCallback(async () => {
    setSitemapValidating(true);
    try {
      const res = await seoSitemapValidate(adminToken);
      setSitemapData(res.data);
    } catch { toast.error('Sitemap validation failed'); }
    finally { setSitemapValidating(false); }
  }, [adminToken]);

  const handleRefreshMeta = useCallback(async () => {
    setRefreshingMeta(true);
    try {
      const res = await adminSeoRefreshMeta(adminToken);
      toast.success(res.data?.message || 'Meta refreshed');
      setTimeout(load, 2000);
    } catch { toast.error('Meta refresh failed'); }
    finally { setRefreshingMeta(false); }
  }, [adminToken, load]);

  const handlePilot = useCallback(async () => {
    setPiloting(true);
    setPilotResult(null);
    try {
      const res = await adminSeoPilot(adminToken, {
        board_name: pilotBoard,
        class_name: pilotClass,
        subject_keyword: pilotSubject,
        chapter_limit: pilotChapters,
      });
      setPilotResult(res.data);
      toast.success(res.data?.message || 'Pilot complete');
      setTimeout(load, 2000);
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Pilot failed';
      toast.error(msg);
      setPilotResult({ error: msg });
    } finally {
      setPiloting(false);
    }
  }, [adminToken, pilotBoard, pilotClass, pilotSubject, pilotChapters, load]);

  const toggleTopic = (id) => setSelectedTopics(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const toggleType  = (id) => setSelectedTypes(prev  => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const filteredTopics = topics.filter(t => {
    if (scopeSubjectOnly && hubCtx?.subjectId && t.subject_id !== hubCtx.subjectId) return false;
    if (!topicSearch.trim()) return true;
    const q = topicSearch.toLowerCase();
    return (t.title || '').toLowerCase().includes(q)
      || (t.subject_name || '').toLowerCase().includes(q)
      || (t.chapter_title || '').toLowerCase().includes(q);
  });

  const getSeoScore = (p) => p.quality_score?.score ?? p.quality?.score ?? 0;
  const getGeoScore = (p) => p.geo_score?.score ?? p.quality?.geo_score ?? 0;
  const getCombinedScore = (p) => {
    if (typeof p.combined_score === 'number') return p.combined_score;
    if (typeof p.quality?.combined_score === 'number') return p.quality.combined_score;
    return Math.round((getSeoScore(p) + getGeoScore(p)) / 2);
  };

  const filteredPages = pages.filter(p => {
    if (pageFilter !== 'all' && p.status !== pageFilter) return false;
    if (minCombined > 0 && getCombinedScore(p) < minCombined) return false;
    if (!pageSearch.trim()) return true;
    const q = pageSearch.toLowerCase();
    return (p.title || '').toLowerCase().includes(q)
      || (p.topic_title || '').toLowerCase().includes(q)
      || (p.subject_name || '').toLowerCase().includes(q)
      || (p.answer_summary || '').toLowerCase().includes(q);
  }).slice().sort((a, b) => {
    if (pageSort === 'combined') return getCombinedScore(b) - getCombinedScore(a);
    if (pageSort === 'geo')      return getGeoScore(b) - getGeoScore(a);
    if (pageSort === 'seo')      return getSeoScore(b) - getSeoScore(a);
    if (pageSort === 'recent') {
      return String(b.updated_at || b.generated_at || '').localeCompare(String(a.updated_at || a.generated_at || ''));
    }
    return 0;
  });

  const publishedCount = pages.filter(p => p.status === 'published').length;
  const draftCount     = pages.filter(p => p.status !== 'published').length;
  const coverage       = topics.length > 0 ? Math.round((publishedCount / (topics.length * 5)) * 100) : 0;

  return {
    tab, setTab, stats, topics, pages, insights, loading, insightsLoading,
    extracting, generating, sitemap, publishing, activeJob, setActiveJob,
    actionLoading, topicSearch, setTopicSearch, pageSearch, setPageSearch,
    pageFilter, setPageFilter, pageSort, setPageSort, minCombined, setMinCombined,
    selectedTopics, selectedTypes,
    piloting, pilotResult, pilotBoard, setPilotBoard, pilotClass, setPilotClass,
    pilotSubject, setPilotSubject, pilotChapters, setPilotChapters,
    hubCtx, scopeSubjectOnly, setScopeSubjectOnly,
    subjectCoverage, coverageLoading, subjectJobs, pipelineSearch, setPipelineSearch,
    reviewQueue, setReviewQueue, reviewLoading, reviewSelected, setReviewSelected,
    flagging, setFlagging, bulkThreshold, setBulkThreshold,
    linksData, linksLoading, injectSlug, setInjectSlug, injecting,
    schemaLoading, schemaSlug, setSchemaSlug, schemaResult,
    sitemapData, sitemapValidating, refreshingMeta,
    load, loadInsights, loadCoverage, loadReviewQueue,
    handleAutoRun, handleExtract, handleGenerate, handleToggleStatus,
    handleDeleteTopic, handleRegenerateSitemap, handleBulkPublish,
    handleInsightAction, handleLinksAnalyze, handleLinksInject,
    handleSchemaInjectSingle, handleSchemaBulk, handleSitemapValidate,
    handleRefreshMeta, handlePilot, handleRunSubject,
    diagnostics, diagnosticsLoading, backfilling,
    handleDiagnoseTopics, handleBackfillNotes,
    schedule, scheduleLoading, loadSchedule,
    toggleTopic, toggleType, filteredTopics, filteredPages,
    publishedCount, draftCount, coverage,
  };
}
