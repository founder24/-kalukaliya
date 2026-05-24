import axios from 'axios';
import { toast } from 'sonner';

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || '';
export const API_BASE = `${BACKEND_URL}/api/v1`;

const _RENDER_URL = (import.meta.env.VITE_RENDER_API_URL || '').replace(/\/+$/, '');
const _WORKER_URL = (import.meta.env.VITE_WORKER_API_URL || '').replace(/\/+$/, '');
const RENDER_API = _RENDER_URL ? `${_RENDER_URL}/api/v1` : API_BASE;
export const WORKER_API = _WORKER_URL ? `${_WORKER_URL}/api/v1` : API_BASE;

let _authToken = null;

export const setAuthToken = (token) => {
  _authToken = token;
};

export function getAnonId() {
  let id = localStorage.getItem('syrabit_anon_id');
  if (!id) {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    id = 'anon_' + Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
    localStorage.setItem('syrabit_anon_id', id);
  }
  return id;
}

const anonHeaders = () => ({ 'x-anon-id': getAnonId() });

const authConfig = () => {
  const config = { withCredentials: true };
  if (_authToken) {
    config.headers = { Authorization: `Bearer ${_authToken}` };
  }
  return config;
};

const RETRY_CODES = new Set([408, 429, 500, 502, 503, 504]);
const MAX_RETRIES = 2;
const RETRY_DELAY_MS = 1000;

axios.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config;
    const retryCount = config?._retryCount || 0;
    if (
      config &&
      retryCount < MAX_RETRIES &&
      error.response &&
      RETRY_CODES.has(error.response.status) &&
      (!config.method || config.method.toLowerCase() === 'get')
    ) {
      config._retryCount = retryCount + 1;
      {
        const delay = RETRY_DELAY_MS * config._retryCount;
        await new Promise(r => setTimeout(r, delay));
        return axios(config);
      }
    }

    if (error.response?.status === 401) {
      const reqUrl = config?.url || '';
      const isAdminContentCall = reqUrl.includes('/admin/content/') || reqUrl.includes('/admin/studio/');
      const isAdminRoute = window.location.pathname.startsWith('/admin') &&
        !window.location.pathname.startsWith('/admin/login');
      if (isAdminRoute && !isAdminContentCall) {
        toast.error('Session expired. Please log in again.');
        window.location.href = '/admin/login';
      }
    }
    return Promise.reject(error);
  }
);

export const apiClient = () =>
  axios.create({ baseURL: API_BASE, withCredentials: true });

export const getRecentMemories = (limit = 5) =>
  axios.get(`${API_BASE}/edu/memory/recent`, { params: { limit }, ...authConfig() });

export const getBoards = () => axios.get(`${WORKER_API}/content/boards`);
export const getClasses = (boardId) => axios.get(`${WORKER_API}/content/classes?board_id=${boardId}`);
export const getStreams = (classId) => axios.get(`${WORKER_API}/content/streams?class_id=${classId}`);
export const getAllSubjects = () => axios.get(`${WORKER_API}/content/subjects`);
export const getSubjectsByCourseType = (boardId) => axios.get(`${WORKER_API}/content/subjects-by-course-type?board_id=${boardId}`);
export const getSubject = (id) => axios.get(`${WORKER_API}/content/subjects/${id}`);
export const getChapters = (subjectId) => axios.get(`${WORKER_API}/content/chapters/${subjectId}`);
export const getChunks = (chapterId) => axios.get(`${WORKER_API}/content/chunks/${chapterId}`);
export const getChapterTopicSummary = (chapterId) => axios.get(`${WORKER_API}/content/chapters/${chapterId}/topic-summary`);
const getChapterTopicContent = (chapterId) => axios.get(`${WORKER_API}/content/chapters/${chapterId}/topic-content`);
const getTopicPage = (topicId, pageType) => axios.get(`${WORKER_API}/content/topic/${topicId}/page/${pageType}`);

// ── Educational Browser (Task #577) ───────────────────────────────────────
export const eduFetchReader = (url, opts = {}) =>
  axios.post(`${API_BASE}/edu/reader/fetch`, { url, bypass_cache: !!opts.bypassCache }, {
    headers: anonHeaders(), withCredentials: true, timeout: 25000,
  });

export const eduCheckUrl = (url) =>
  axios.post(`${API_BASE}/edu/check-url`, { url }, { headers: anonHeaders() });

export const eduGetAllowlist = () =>
  axios.get(`${API_BASE}/edu/allowlist`);

export const eduRequestSite = (domain, reason = '') =>
  axios.post(`${API_BASE}/edu/request-site`, { domain, reason }, {
    headers: anonHeaders(), withCredentials: true,
  });

export const eduEducatorSubmitSite = (domain, note = '') =>
  axios.post(`${API_BASE}/edu/educator/submit-site`, { domain, note }, {
    headers: anonHeaders(), withCredentials: true,
  });

export const eduEducatorAppealRejection = (domain, reason = '', probe = null, probeError = '') =>
  axios.post(
    `${API_BASE}/edu/educator/appeal-rejection`,
    { domain, reason, probe: probe || {}, probe_error: probeError || '' },
    { headers: anonHeaders(), withCredentials: true },
  );

export const eduEducatorMySubmissions = (limit = 10) =>
  axios.get(`${API_BASE}/edu/educator/my-submissions`, {
    params: { limit }, headers: anonHeaders(), withCredentials: true,
  });

export const eduEducatorRemoveMySubmission = (domain) =>
  axios.delete(`${API_BASE}/edu/educator/my-submissions/${encodeURIComponent(domain)}`, {
    headers: anonHeaders(), withCredentials: true,
  });

export const eduEducatorMyAppeals = (limit = 10) =>
  axios.get(`${API_BASE}/edu/educator/my-appeals`, {
    params: { limit }, headers: anonHeaders(), withCredentials: true,
  });

export const eduLoadState = () =>
  axios.get(`${API_BASE}/edu/state`, { headers: anonHeaders(), withCredentials: true });

export const eduSaveState = (state) =>
  axios.post(`${API_BASE}/edu/state`, state, { headers: anonHeaders(), withCredentials: true });

export const eduGroundedAnswerUrl = () => `${API_BASE}/edu/grounded-answer`;

export const getConversations = () =>
  axios.get(`${API_BASE}/conversations`, authConfig());

export const getConversation = (id) =>
  axios.get(`${API_BASE}/conversations/${id}`, authConfig());

export const deleteConversation = (id) =>
  axios.delete(`${API_BASE}/conversations/${id}`, authConfig());

export const updateConversation = (id, data) =>
  axios.patch(`${API_BASE}/conversations/${id}`, data, authConfig());

export const getAnonConversations = () =>
  axios.get(`${API_BASE}/conversations/anon`, { headers: anonHeaders() });

export const getAnonConversation = (id) =>
  axios.get(`${API_BASE}/conversations/anon/${id}`, { headers: anonHeaders() });

export const deleteAnonConversation = (id) =>
  axios.delete(`${API_BASE}/conversations/anon/${id}`, { headers: anonHeaders() });

export const saveOnboarding = (data) =>
  axios.post(`${API_BASE}/user/onboarding`, data, authConfig());

export const adminLogin = (email, password, turnstileToken = '') => {
  const headers = {};
  if (turnstileToken) headers['x-turnstile-token'] = turnstileToken;
  return axios.post(
    `${API_BASE}/admin/login`,
    { email, password },
    { withCredentials: true, headers },
  );
};

export const adminHeaders = (token) => {
  const isRealJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isRealJwt ? { Authorization: `Bearer ${token}` } : {};
};

export const adminVerify = (token) =>
  axios.get(`${API_BASE}/admin/verify`, {
    ...(token ? { headers: adminHeaders(token) } : {}),
    withCredentials: true,
  });

export const adminLogout = () =>
  axios.post(`${API_BASE}/admin/logout`, {}, { withCredentials: true });

export const adminGetDashboard = (token) =>
  axios.get(`${API_BASE}/admin/dashboard`, { headers: adminHeaders(token), withCredentials: true });

// Task #701 — list subjects served via the relaxed status filter so
// admins can flip them to "published" and silence the WARN logs.
export const adminGetDraftServedSubjects = (token) =>
  axios.get(`${API_BASE}/admin/content/draft-served-subjects`, {
    headers: adminHeaders(token),
    withCredentials: true,
  });

export const adminPublishSubject = (token, subjectId) =>
  axios.patch(
    `${API_BASE}/admin/content/subjects/${subjectId}`,
    { status: 'published' },
    { headers: adminHeaders(token), withCredentials: true },
  );

// Task #940 — Entity SEO + Knowledge Graph health panel. Mirrors the
// FastAPI router shape from `routes/admin_entity_seo.py`:
//   GET  /admin/seo/entity/status     — current snapshot + WoW deltas
//   GET  /admin/seo/entity/history    — recent snapshots for the chart
//   POST /admin/seo/entity/refresh    — manual re-probe (admin override)
export const adminEntitySeoStatus = (token) =>
  axios.get(`${API_BASE}/admin/seo/entity/status`, {
    headers: adminHeaders(token),
    withCredentials: true,
  });

export const adminEntitySeoHistory = (token, limit = 20) =>
  axios.get(`${API_BASE}/admin/seo/entity/history`, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { limit },
  });

export const adminEntitySeoRefresh = (token) =>
  axios.post(`${API_BASE}/admin/seo/entity/refresh`, {}, {
    headers: adminHeaders(token),
    withCredentials: true,
    timeout: 60000,
  });

export const adminSeoHealthHistory = (token, limit = 168) =>
  axios.get(`${API_BASE}/admin/seo/health-history`, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { limit },
  });

export const adminSeoHealthSnapshotNow = (token) =>
  axios.post(`${API_BASE}/admin/seo/health-snapshot`, {}, {
    headers: adminHeaders(token),
    withCredentials: true,
  });

export const seoHealthLive = () =>
  axios.get(`${API_BASE}/seo/health`, { withCredentials: true });

// Task #345: deep-scan a single sitemap and return ALL failing URLs
// (not just the 10-sample slice surfaced by /seo/health). Implemented
// as a `?deep_scan=` variant on the same /seo/health endpoint per the
// reviewer-approved contract; admin auth is enforced server-side
// because a full scan probes up to 500 URLs per call.
export const seoHealthDeepScan = (token, sitemap) =>
  axios.get(`${API_BASE}/seo/health`, {
    headers: adminHeaders(token),
    params: { deep_scan: sitemap },
    withCredentials: true,
    timeout: 60000,
  });

// Task #350: surface auto-deep-scan results from the on-call alert loop
// (Task #347) on the admin dashboard so the on-call admin sees the
// true blast radius of an outage the moment they open the dashboard
// from the alert email — without having to re-click "Deep scan" per
// sitemap and wait again. Reads from db.alerts via the backend.
export const adminSeoDeepScanHistory = (token, limit = 50) =>
  axios.get(`${API_BASE}/admin/seo/deep-scan-history`, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { limit },
  });

export const adminGetUsers = (token, params = {}) =>
  axios.get(`${API_BASE}/admin/users`, { headers: adminHeaders(token), withCredentials: true, params });

export const adminUpdateUserStatus = (token, userId, status) =>
  axios.patch(`${API_BASE}/admin/users/${userId}/status`, { status }, { headers: adminHeaders(token), withCredentials: true });

export const adminUpdateUserPlan = (token, userId, plan) =>
  axios.patch(`${API_BASE}/admin/users/${userId}/plan`, { plan }, { headers: adminHeaders(token), withCredentials: true });

// Task #591: promote a trusted user to the 'educator' role (or revert to 'student').
export const adminUpdateUserRole = (token, userId, role, reason) =>
  axios.patch(
    `${API_BASE}/admin/users/${userId}/role`,
    { role, ...(reason ? { reason } : {}) },
    { headers: adminHeaders(token), withCredentials: true },
  );

export const adminGetConversations = (token) =>
  axios.get(`${API_BASE}/admin/conversations`, { headers: adminHeaders(token), withCredentials: true });

export const adminGetAnalytics = (token, days = 30) =>
  axios.get(`${API_BASE}/admin/analytics`, { headers: adminHeaders(token), withCredentials: true, params: { days } });

export const adminGetDailyAnalytics = (token, days = 30) =>
  axios.get(`${API_BASE}/admin/analytics/daily`, { headers: adminHeaders(token), withCredentials: true, params: { days } });

// Task #456: Cloudflare-Analytics token health for the admin banner.
// `cf-status` returns { configured, auth_ok, needs_rotation, last_error,
// last_check_at, blocked_for_seconds, consecutive_failures, rotation_hint }.
// `cf-recheck` resets the auth circuit-breaker and re-probes immediately
// (no Railway restart needed after rotating CF_ANALYTICS_API_TOKEN).
export const adminGetCfStatus = (token) =>
  axios.get(`${API_BASE}/admin/analytics/cf-status`, { headers: adminHeaders(token), withCredentials: true });

export const adminCfRecheck = (token) =>
  axios.post(`${API_BASE}/admin/analytics/cf-recheck`, {}, { headers: adminHeaders(token), withCredentials: true });

// Cloudflare-mirror Account Analytics overview for the Traffic card.
// `range` is "24h" | "7d" | "30d".
export const adminGetCfOverview = (token, range = '7d') =>
  axios.get(`${API_BASE}/admin/analytics/cf-overview`, {
    params: { range },
    headers: adminHeaders(token),
    withCredentials: true,
  });

export const adminGetBotTraffic = (token, period = '24h') =>
  axios.get(`${API_BASE}/admin/analytics/bot-traffic`, {
    params: { period },
    headers: adminHeaders(token),
    withCredentials: true,
  });

// Task #408: hydrate-lifecycle / stale-build telemetry tile
export const adminGetHydrateStats = (token, days = 7) =>
  axios.get(`${API_BASE}/admin/analytics/hydrate-stats`, { headers: adminHeaders(token), withCredentials: true, params: { days } });

// Task #654 (Trustpilot per #724/#726): review prompt funnel tile
export const adminGetReviewPromptStats = (token, days = 30) =>
  axios.get(`${API_BASE}/admin/analytics/review-prompt-stats`, { headers: adminHeaders(token), withCredentials: true, params: { days } });

// Task #681: per-reason baseline mean CTR + stddev + current z-score
// (the same noise band the auto-tuned collapse alert uses, surfaced
// next to each row so admins can eyeball volatility ahead of an alert).
export const adminGetReviewPromptBaselineNoise = (token, windowDays = 7) =>
  axios.get(
    `${API_BASE}/admin/analytics/review-prompt-stats/baseline-noise`,
    { headers: adminHeaders(token), withCredentials: true, params: { window_days: windowDays } },
  );

// Task #662: per-reason 8-week trend (drill-down sparkline)
// Task #673: optional `compare` reason overlays a second series on the
// same chart so admins can spot whether a CTR dip is reason-specific.
export const adminGetReviewPromptByReasonTrend = (token, reason, weeks = 8, compare = null) => {
  const params = { reason, weeks };
  if (compare) params.compare = compare;
  return axios.get(`${API_BASE}/admin/analytics/review-prompt-stats/by-reason-trend`, {
    headers: adminHeaders(token),
    withCredentials: true,
    params,
  });
};

export const adminGetRevenue = (token, days = 30) =>
  axios.get(`${API_BASE}/admin/analytics/revenue`, { headers: adminHeaders(token), withCredentials: true, params: { days } });

export const adminGetPredictor = (token) =>
  axios.get(`${API_BASE}/admin/analytics/predictor`, { headers: adminHeaders(token), withCredentials: true });

export const adminGetGA4Status = (token) =>
  axios.get(`${API_BASE}/admin/ga4/status`, { headers: adminHeaders(token), withCredentials: true });

export const adminGetGA4AuthUrl = (token, redirectUri) =>
  axios.get(`${API_BASE}/admin/ga4/auth-url`, { headers: adminHeaders(token), withCredentials: true, params: { redirect_uri: redirectUri } });


export const adminTestGA4 = (token) =>
  axios.get(`${API_BASE}/admin/ga4/test`, { headers: adminHeaders(token), withCredentials: true });



export const adminGetContentCardViews = (token, days = 0) =>
  axios.get(`${API_BASE}/admin/analytics/content-card-views`, { headers: adminHeaders(token), withCredentials: true, params: { days } });

// ── Ads (Task #551) ─────────────────────────────────────────────────────────
export const adminGetAdsOverview = (token, days = 30) =>
  axios.get(`${API_BASE}/admin/ads/overview`, { headers: adminHeaders(token), withCredentials: true, params: { days } });

export const adminListAdEarnings = (token, days = 30, network) =>
  axios.get(`${API_BASE}/admin/ads/earnings`, {
    headers: adminHeaders(token), withCredentials: true,
    params: network ? { days, network } : { days },
  });

export const adminAddAdEarning = (token, entry) =>
  axios.post(`${API_BASE}/admin/ads/earnings`, entry, { headers: adminHeaders(token), withCredentials: true });

export const adminDeleteAdEarning = (token, id) =>
  axios.delete(`${API_BASE}/admin/ads/earnings/${id}`, { headers: adminHeaders(token), withCredentials: true });

export const adminUploadAdEarningsCsv = (token, network, file) => {
  const fd = new FormData();
  fd.append('network', network);
  fd.append('file', file);
  return axios.post(`${API_BASE}/admin/ads/earnings/csv`, fd, {
    headers: { ...adminHeaders(token), 'Content-Type': 'multipart/form-data' },
    withCredentials: true,
  });
};

export const adminGetAdsenseStatus = (token) =>
  axios.get(`${API_BASE}/admin/ads/adsense/status`, { headers: adminHeaders(token), withCredentials: true });

export const adminAdsenseSync = (token, days = 7) =>
  axios.post(`${API_BASE}/admin/ads/adsense/sync`, {}, {
    headers: adminHeaders(token), withCredentials: true, params: { days },
  });

export const adminGetSettings = (token) =>
  axios.get(`${API_BASE}/admin/settings`, { headers: adminHeaders(token), withCredentials: true });

export const adminGetDiagnostics = (token) =>
  axios.get(`${API_BASE}/admin/diagnostics`, { headers: adminHeaders(token), withCredentials: true });

export const adminDisableBreakGlass = (token) =>
  axios.post(`${API_BASE}/admin/break-glass/disable`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminUpdateSettings = (token, data) =>
  axios.patch(`${API_BASE}/admin/settings`, data, { headers: adminHeaders(token), withCredentials: true });

export const adminGetRoadmap = (token) =>
  axios.get(`${API_BASE}/admin/roadmap`, { headers: adminHeaders(token), withCredentials: true });

export const adminCreateRoadmapItem = (token, data) =>
  axios.post(`${API_BASE}/admin/roadmap`, data, { headers: adminHeaders(token), withCredentials: true });

export const adminDeleteRoadmapItem = (token, id) =>
  axios.delete(`${API_BASE}/admin/roadmap/${id}`, { headers: adminHeaders(token), withCredentials: true });


export const adminGetPlanConfig = (token) =>
  axios.get(`${API_BASE}/admin/plan-config`, { headers: adminHeaders(token), withCredentials: true });

export const adminUpdatePlanConfig = (token, data) =>
  axios.put(`${API_BASE}/admin/plan-config`, data, { headers: adminHeaders(token), withCredentials: true });

export const adminGetApiConfig = (token) =>
  axios.get(`${API_BASE}/admin/api-config`, { headers: adminHeaders(token), withCredentials: true });

export const adminUpdateApiConfig = (token, data) =>
  axios.put(`${API_BASE}/admin/api-config`, data, { headers: adminHeaders(token), withCredentials: true });

export const adminUpdateRoadmapItem = (token, id, data) =>
  axios.patch(`${API_BASE}/admin/roadmap/${id}`, data, { headers: adminHeaders(token), withCredentials: true });

export const adminGetActivityLog = (token) =>
  axios.get(`${API_BASE}/admin/activity-log`, { headers: adminHeaders(token), withCredentials: true });

// Task #276 — Syra voice assistant. Posts the spoken transcript and the
// currently-active admin section to the backend, which routes through
// the english_rag_chat provider pool and returns a structured action
// (navigate / scroll / fetch / answer) for the orb to execute locally.
// Task #298 — chat now ships rolling history + screen context (selected
// entity, active filters, visible error string) so Syra can resolve
// pronouns and operate on what the operator can actually see.
export const adminSyraChat = (transcript, options, token) => {
  const opts = options || {};
  const activeSection = typeof opts === 'string' ? opts : opts.activeSection;
  const history = (opts.history || []).slice(-8).map((t) => ({
    role: t.role,
    content: String(t.content || '').slice(0, 1200),
  }));
  const selected = opts.selectedEntity || null;
  return axios.post(
    `${API_BASE}/admin/syra/chat`,
    {
      transcript,
      history,
      context: {
        active_section: activeSection || 'dashboard',
        selected_entity: selected,
        filters: opts.filters || null,
        visible_error: opts.visibleError || null,
      },
    },
    { headers: adminHeaders(token), withCredentials: true, timeout: 30000 },
  );
};

// Task #298 — list registered Syra write actions (used by the orb's
// settings panel + as an introspection seam for tests).
export const adminSyraActions = (token) =>
  axios.get(`${API_BASE}/admin/syra/actions`, {
    headers: adminHeaders(token), withCredentials: true,
  });

// Task #298 — execute a registered Syra write action. The backend
// rejects unknown ids and requires `confirmed=true` for destructive
// actions, so the frontend gates destructive paths on the confirm card.
export const adminSyraExecuteAction = (token, actionId, params, confirmed) =>
  axios.post(
    `${API_BASE}/admin/syra/execute-action`,
    { action_id: actionId, params: params || {}, confirmed: !!confirmed },
    { headers: adminHeaders(token), withCredentials: true, timeout: 30000 },
  );

// Task #298 — per-admin Syra preferences (server-side, so two operators
// sharing a workstation don't clobber each other's mute / wake-word /
// persona choices). The frontend still mirrors the response into a
// namespaced localStorage entry for offline reads.
export const adminSyraGetPrefs = (token) =>
  axios.get(`${API_BASE}/admin/syra/prefs`, {
    headers: adminHeaders(token), withCredentials: true, timeout: 10000,
  });

export const adminSyraSavePrefs = (token, prefs) =>
  axios.put(
    `${API_BASE}/admin/syra/prefs`,
    { prefs: prefs || {} },
    { headers: adminHeaders(token), withCredentials: true, timeout: 10000 },
  );

// Task #298 — daily briefing: open alerts + cron failures + negative
// feedback + new signups, packaged as a short paragraph the orb reads
// out on the first open of each UTC day.
export const adminSyraBriefing = (token) =>
  axios.get(`${API_BASE}/admin/syra/briefing`, {
    headers: adminHeaders(token), withCredentials: true, timeout: 15000,
  });

// Task #voice-agent — Deepgram-backed STT for the Syra orb. Posts a
// short recorded blob (webm/ogg/mp3/wav) and returns
// { transcript, language }. Strictly admin-gated.
export const adminSyraSTT = (audioBlob, language, token) => {
  const fd = new FormData();
  const ext = (audioBlob.type || '').includes('ogg') ? 'ogg' : 'webm';
  fd.append('audio', audioBlob, `syra.${ext}`);
  fd.append('language', language || 'en');
  return axios.post(`${API_BASE}/admin/syra/stt`, fd, {
    headers: { ...adminHeaders(token) },
    withCredentials: true,
    timeout: 30000,
  });
};

// Task #voice-agent — Deepgram Aura-2 TTS for the Syra orb. Returns the
// raw MP3 bytes as a Blob URL the caller can hand to <audio>. The
// `responseType: 'blob'` opt-in is required because axios defaults to
// JSON-parsing the response.
export const adminSyraTTS = async (text, language, token, voice) => {
  const res = await axios.post(
    `${API_BASE}/admin/syra/tts`,
    { text, language: language || 'en', voice: voice || null },
    {
      headers: { ...adminHeaders(token), 'Content-Type': 'application/json' },
      withCredentials: true,
      responseType: 'blob',
      timeout: 30000,
    },
  );
  const blob = new Blob([res.data], { type: 'audio/mpeg' });
  return URL.createObjectURL(blob);
};

const getSeoPage = (board, classSlug, subjectSlug, topicSlug, pageType) => {
  let url = `${WORKER_API}/seo/page/${board}/${classSlug}/${subjectSlug}/${topicSlug}`;
  if (pageType && pageType !== 'notes') url += `/${pageType}`;
  return axios.get(url);
};

const getSeoPageBundle = (board, classSlug, subjectSlug, topicSlug, pageType) =>
  axios.get(`${WORKER_API}/seo/page-bundle/${board}/${classSlug}/${subjectSlug}/${topicSlug}`, {
    params: pageType && pageType !== 'notes' ? { pt: pageType } : undefined,
  });

const getSeoPageTypes = (board, classSlug, subjectSlug, topicSlug) =>
  axios.get(`${WORKER_API}/seo/page-types/${board}/${classSlug}/${subjectSlug}/${topicSlug}`);

const getSeoRelated = (topicSlug) =>
  axios.get(`${WORKER_API}/seo/related/${topicSlug}`);

const getChapterBySlug = (board, classSlug, subjectSlug, chapterSlug) =>
  axios.get(`${WORKER_API}/content/chapter-by-slug/${board}/${classSlug}/${subjectSlug}/${chapterSlug}`);

// ── Admin SEO management ──────────────────────────────────────────────────────

export const adminSeoStats = (token) =>
  axios.get(`${API_BASE}/seo/stats`, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoListTopics = (token, params = {}) =>
  axios.get(`${API_BASE}/seo/topics`, { headers: adminHeaders(token), withCredentials: true, params });

export const adminSeoCreateTopic = (token, data) =>
  axios.post(`${API_BASE}/seo/topics`, data, { headers: adminHeaders(token), withCredentials: true });


export const adminSeoDeleteTopic = (token, topicId) =>
  axios.delete(`${API_BASE}/seo/topics/${topicId}`, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoExtractTopics = (token, subjectId, force = false) => {
  const params = {};
  if (subjectId) params.subject_id = subjectId;
  if (force) params.force = true;
  return axios.post(`${API_BASE}/seo/extract-topics`, {}, { headers: adminHeaders(token), withCredentials: true, params });
};

export const adminSeoGenerate = (token, data) =>
  axios.post(`${API_BASE}/seo/generate`, data, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoListPages = (token, params = {}) =>
  axios.get(`${API_BASE}/seo/pages`, { headers: adminHeaders(token), withCredentials: true, params });

export const adminSeoUpdatePageStatus = (token, pageId, status) =>
  axios.patch(`${API_BASE}/seo/pages/${pageId}/status`, null, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { status },
  });

export const adminSeoRegenerateSitemap = (token) =>
  axios.post(`${API_BASE}/admin/content/regenerate-sitemap`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoPilot = (token, params = {}) =>
  axios.post(`${API_BASE}/seo/pilot`, null, {
    headers: adminHeaders(token),
    withCredentials: true,
    params,
  });

export const adminSeoAutoRun = (token, pageTypes = null) =>
  axios.post(`${API_BASE}/seo/auto-run`, pageTypes ? { page_types: pageTypes } : {}, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoJobStatus = (token, jobId) =>
  axios.get(`${API_BASE}/seo/jobs/${jobId}`, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoDiagnoseTopics = (token, params = {}) =>
  axios.get(`${API_BASE}/seo/diagnose-topics`, { headers: adminHeaders(token), withCredentials: true, params });

export const adminSeoBackfillNotes = (token) =>
  axios.post(`${API_BASE}/seo/backfill-notes`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoInsights = (token) =>
  axios.get(`${API_BASE}/seo/insights`, { headers: adminHeaders(token), withCredentials: true });

const adminSeoExpand = (token, boardSlug, pageTypes = null) =>
  axios.post(`${API_BASE}/seo/expand/${boardSlug}`, pageTypes ? { page_types: pageTypes } : {}, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoBulkPublish = (token, pageType = null, subjectId = null) =>
  axios.post(`${API_BASE}/seo/bulk-publish`, null, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { ...(pageType ? { page_type: pageType } : {}), ...(subjectId ? { subject_id: subjectId } : {}) },
  });

export const adminSeoSubjectCoverage = (token) =>
  axios.get(`${API_BASE}/seo/subject-coverage`, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoAutoPublishSchedule = (token) =>
  axios.get(`${API_BASE}/seo/auto-publish/schedule`, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoRunSubject = (token, subjectId, force = false, pageTypes = null) =>
  axios.post(`${API_BASE}/seo/run-subject`, pageTypes ? { page_types: pageTypes } : {}, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { subject_id: subjectId, ...(force ? { force: true } : {}) },
  });

export const adminSeoRefreshMeta = (token) =>
  axios.post(`${API_BASE}/seo/refresh-meta`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoGoogleIndexingStats = (token) =>
  axios.get(`${API_BASE}/admin/seo/google-indexing-stats`, { headers: adminHeaders(token), withCredentials: true });

export const adminTopicDiscoveryRuns = (token, limit = 20) =>
  axios.get(`${API_BASE}/admin/seo/topic-discovery/runs`, {
    headers: adminHeaders(token), withCredentials: true, params: { limit },
  });

export const adminTopicDiscoveryCandidates = (token, { runId = null, decision = null, limit = 100, skip = 0 } = {}) => {
  const params = { limit, skip };
  if (runId) params.run_id = runId;
  if (decision) params.decision = decision;
  return axios.get(`${API_BASE}/admin/seo/topic-discovery/candidates`, {
    headers: adminHeaders(token), withCredentials: true, params,
  });
};

export const adminTopicDiscoveryRunNow = (token) =>
  axios.post(`${API_BASE}/admin/seo/topic-discovery/run-now`, null, {
    headers: adminHeaders(token), withCredentials: true,
  });

export const adminTopicDiscoveryOverride = (token, candidateId, decision, reason = '') =>
  axios.post(
    `${API_BASE}/admin/seo/topic-discovery/${encodeURIComponent(candidateId)}/override`,
    { decision, reason },
    { headers: adminHeaders(token), withCredentials: true },
  );

// Task #938 — closed-loop content remediation agent.
export const adminSeoRemediationStatus = (token) =>
  axios.get(`${API_BASE}/admin/seo/remediation/status`, {
    headers: adminHeaders(token), withCredentials: true,
  });

export const adminSeoRemediationHistory = (token, { days = 7, limit = 50, action = null } = {}) => {
  const params = { days, limit };
  if (action) params.action = action;
  return axios.get(`${API_BASE}/admin/seo/remediation/history`, {
    headers: adminHeaders(token), withCredentials: true, params,
  });
};

export const adminSeoRemediationPromote = (token, recId) =>
  axios.post(
    `${API_BASE}/admin/seo/remediation/${encodeURIComponent(recId)}/promote`,
    null,
    { headers: adminHeaders(token), withCredentials: true },
  );

export const adminSeoRemediationTrigger = (token, payload) =>
  axios.post(`${API_BASE}/admin/seo/remediation/trigger`, payload, {
    headers: adminHeaders(token), withCredentials: true,
  });

export const adminSeoRemediationCircuitReset = (token) =>
  axios.post(`${API_BASE}/admin/seo/remediation/circuit/reset`, null, {
    headers: adminHeaders(token), withCredentials: true,
  });

// Task #939 — agentic internal-linker.
export const adminSeoInternalLinksStatus = (token) =>
  axios.get(`${API_BASE}/admin/seo/internal-links/status`, {
    headers: adminHeaders(token), withCredentials: true,
  });

export const adminSeoInternalLinksPending = (token, { limit = 50 } = {}) =>
  axios.get(`${API_BASE}/admin/seo/internal-links/pending`, {
    headers: adminHeaders(token), withCredentials: true, params: { limit },
  });

export const adminSeoInternalLinksHistory = (token, { days = 7, limit = 100, action = null } = {}) => {
  const params = { days, limit };
  if (action) params.action = action;
  return axios.get(`${API_BASE}/admin/seo/internal-links/history`, {
    headers: adminHeaders(token), withCredentials: true, params,
  });
};

export const adminSeoInternalLinksApprove = (token, recId) =>
  axios.post(
    `${API_BASE}/admin/seo/internal-links/${encodeURIComponent(recId)}/approve`,
    null,
    { headers: adminHeaders(token), withCredentials: true },
  );

export const adminSeoInternalLinksReject = (token, recId) =>
  axios.post(
    `${API_BASE}/admin/seo/internal-links/${encodeURIComponent(recId)}/reject`,
    null,
    { headers: adminHeaders(token), withCredentials: true },
  );

export const adminSeoInternalLinksRevert = (token, recId) =>
  axios.post(
    `${API_BASE}/admin/seo/internal-links/${encodeURIComponent(recId)}/revert`,
    null,
    { headers: adminHeaders(token), withCredentials: true },
  );

export const adminSeoInternalLinksTrigger = (token, payload) =>
  axios.post(`${API_BASE}/admin/seo/internal-links/trigger`, payload, {
    headers: adminHeaders(token), withCredentials: true,
  });

export const adminSeoReviewQueue = (token, status = 'draft', limit = 200) =>
  axios.get(`${API_BASE}/seo/review-queue`, { headers: adminHeaders(token), withCredentials: true, params: { status, limit } });

export const adminSeoBulkReviewAction = (token, action, pageIds = [], minScore = null) => {
  const params = new URLSearchParams();
  params.append('action', action);
  pageIds.forEach(id => params.append('page_ids', id));
  if (minScore != null) params.append('min_score', String(minScore));
  return axios.post(`${API_BASE}/seo/review-queue/bulk-action`, null, {
    headers: adminHeaders(token), withCredentials: true, params,
  });
};

export const adminSeoFlagLowQuality = (token) =>
  axios.post(`${API_BASE}/seo/flag-low-quality`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoQualityAudit = (token, { unpublishBelow = 90, dryRun = false } = {}) =>
  axios.post(`${API_BASE}/seo/quality-audit`, null, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { unpublish_below: unpublishBelow, dry_run: dryRun },
  });

export const adminSeoQualitySummary = (token) =>
  axios.get(`${API_BASE}/seo/quality-summary`, { headers: adminHeaders(token), withCredentials: true });

export const adminSeoDuplicateScan = (token, { similarityThreshold = 0.8, scope = 'subject' } = {}) =>
  axios.post(`${API_BASE}/seo/duplicate-scan`, null, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { similarity_threshold: similarityThreshold, scope },
  });

export const adminSeoDuplicatePairs = (token, status = 'open', limit = 100) =>
  axios.get(`${API_BASE}/seo/duplicate-pairs`, {
    headers: adminHeaders(token), withCredentials: true,
    params: { status, limit },
  });

export const adminSeoResolveDuplicate = (token, pairId, action = 'ignore') =>
  axios.post(`${API_BASE}/seo/duplicate-pairs/${pairId}/resolve`, null, {
    headers: adminHeaders(token), withCredentials: true,
    params: { action },
  });

/**
 * Task #155 — Request a unique per-user Trustpilot invitation link from
 * the backend. Authenticated via the session cookie so the server can
 * attach the user's email/name.
 *
 * Intentionally throws on failure rather than silently returning a fallback,
 * so the caller (TrustpilotReviewModal) can distinguish error types and show
 * an appropriate user-facing notice:
 *   - AxiosError with response.status === 401 → user is not authenticated
 *   - Any other error → network / Trustpilot API failure
 * The caller supplies the fallback URL and handles all error cases.
 *
 * @returns {Promise<string>} The personalised invitation URL.
 * @throws {import('axios').AxiosError} On HTTP errors or network failure.
 */
export async function generateTrustpilotInvitationLink() {
  const res = await axios.post(
    `${API_BASE}/trustpilot/invitation-link`,
    {},
    { withCredentials: true },
  );
  return res.data?.url || 'https://www.trustpilot.com/review/syrabit.ai';
}

export const seoRelatedByChapter = (chapterId, excludeTopicId = null, limit = 5) =>
  axios.get(`${API_BASE}/seo/related-by-chapter/${chapterId}`, {
    params: { limit, ...(excludeTopicId ? { exclude_topic_id: excludeTopicId } : {}) },
  });



// ── Payments ─────────────────────────────────────────────────────────────────

export const createPaymentOrder = (plan) =>
  axios.post(`${API_BASE}/payments/create-order`, { plan }, authConfig());

export const verifyPayment = (data) =>
  axios.post(`${API_BASE}/payments/verify`, data, authConfig());

export const recoverPayment = () =>
  axios.post(`${API_BASE}/payments/recover`, {}, authConfig());

export const createCreditTopUp = (credits) =>
  axios.post(`${API_BASE}/payments/credit-topup`, { credits, provider: 'razorpay' }, authConfig());

export const verifyCreditTopUp = (data) =>
  axios.post(`${API_BASE}/payments/credit-topup/verify`, data, authConfig());

export const getPaymentHistory = () =>
  axios.get(`${API_BASE}/user/payments`, authConfig());

export const requestRefund = (paymentId, reason = '') =>
  axios.post(`${API_BASE}/payments/refund-request`, { payment_id: paymentId, reason }, authConfig());

// ── Vertex AI / Gemini Services ──────────────────────────────────────────────
export const vertexHealth = (token) =>
  axios.get(`${API_BASE}/admin/vertex/health`, { headers: adminHeaders(token), withCredentials: true });

export const vertexProviderRouting = (token) =>
  axios.get(`${API_BASE}/admin/vertex/provider-routing`, { headers: adminHeaders(token), withCredentials: true });

export const vertexTranslate = (token, text, target_lang = 'as', source_lang = 'en') =>
  axios.post(`${API_BASE}/admin/vertex/translate`, { text, target_lang, source_lang }, { headers: adminHeaders(token), withCredentials: true });

export const vertexSemanticSearch = (token, query, top_k = 10) =>
  axios.post(`${API_BASE}/admin/vertex/semantic-search`, { query, top_k }, { headers: adminHeaders(token), withCredentials: true });

export const vertexQualityScore = (token, content, page_type, topic, subject) =>
  axios.post(`${API_BASE}/admin/vertex/quality-score`, { content, page_type, topic, subject }, { headers: adminHeaders(token), withCredentials: true });

export const vertexSuggestTopics = (token, subject, class_name, board = 'AHSEC') =>
  axios.post(`${API_BASE}/admin/vertex/suggest-topics`, { subject, class_name, board }, { headers: adminHeaders(token), withCredentials: true });

export const vertexSeoMeta = (token, data) =>
  axios.post(`${API_BASE}/admin/vertex/seo-meta`, data, { headers: adminHeaders(token), withCredentials: true });

export const vertexContentGaps = (token) =>
  axios.get(`${API_BASE}/admin/vertex/content-gaps`, { headers: adminHeaders(token), withCredentials: true });

export const vertexOcr = (token, formData) =>
  axios.post(`${API_BASE}/admin/vertex/ocr`, formData, {
    headers: { ...adminHeaders(token), 'Content-Type': 'multipart/form-data' },
    withCredentials: true,
  });

export const vertexNlpConcepts = (token, text, subject, class_name) =>
  axios.post(`${API_BASE}/admin/vertex/nlp-concepts`, { text, subject, class_name },
    { headers: adminHeaders(token), withCredentials: true });

export const vertexFlashcards = (token, text, subject, class_name, count = 10) =>
  axios.post(`${API_BASE}/admin/vertex/flashcards`, { text, subject, class_name, count },
    { headers: adminHeaders(token), withCredentials: true });

export const vertexMcqGenerator = (token, text, subject, class_name, count = 10, difficulty = 'mixed') =>
  axios.post(`${API_BASE}/admin/vertex/mcq-generator`, { text, subject, class_name, count, difficulty },
    { headers: adminHeaders(token), withCredentials: true });

// ── Upgrade Wave API Helpers ──────────────────────────────────────────────────
export const seoInternalLinksAnalyze = (token) =>
  axios.get(`${API_BASE}/admin/seo/internal-links/analyze`, { headers: adminHeaders(token), withCredentials: true });

export const seoInternalLinksInject = (token, slug) =>
  axios.post(`${API_BASE}/admin/seo/internal-links/inject/${slug}`, {}, { headers: adminHeaders(token), withCredentials: true });

export const seoInjectSchema = (token, slug) =>
  axios.post(`${API_BASE}/admin/seo/inject-schema/${slug}`, {}, { headers: adminHeaders(token), withCredentials: true });

export const seoInjectSchemaBulk = (token) =>
  axios.post(`${API_BASE}/admin/seo/inject-schema-bulk`, {}, { headers: adminHeaders(token), withCredentials: true });

export const seoPipelineStatus = (token) =>
  axios.get(`${API_BASE}/admin/seo/pipeline-status`, { headers: adminHeaders(token), withCredentials: true });

export const seoSitemapValidate = (token) =>
  axios.get(`${API_BASE}/admin/seo/sitemap-validate`, { headers: adminHeaders(token), withCredentials: true });

export const extractFaqs = (token, limit = 100) =>
  axios.get(`${API_BASE}/admin/conversations/extract-faqs?limit=${limit}`, { headers: adminHeaders(token), withCredentials: true });

export const conversationsSentiment = (token) =>
  axios.get(`${API_BASE}/admin/conversations/sentiment`, { headers: adminHeaders(token), withCredentials: true });

export const syncConversations = (token) =>
  axios.post(`${API_BASE}/admin/sync-conversations`, {}, { headers: adminHeaders(token), withCredentials: true });

export const pageConversions = (token, days = 30) =>
  axios.get(`${API_BASE}/admin/analytics/page-conversions?days=${days}`, { headers: adminHeaders(token), withCredentials: true });

export const churnRisk = (token) =>
  axios.get(`${API_BASE}/admin/users/churn-risk`, { headers: adminHeaders(token), withCredentials: true });

export const llmCosts = (token, days = 7) =>
  axios.get(`${API_BASE}/admin/health/llm-costs?days=${days}`, { headers: adminHeaders(token), withCredentials: true });

export const getNotificationTriggers = (token) =>
  axios.get(`${API_BASE}/admin/notifications/triggers`, { headers: adminHeaders(token), withCredentials: true });

export const createNotificationTrigger = (token, data) =>
  axios.post(`${API_BASE}/admin/notifications/triggers`, data, { headers: adminHeaders(token), withCredentials: true });

export const updateNotificationTrigger = (token, id, data) =>
  axios.patch(`${API_BASE}/admin/notifications/triggers/${id}`, data, { headers: adminHeaders(token), withCredentials: true });

export const deleteNotificationTrigger = (token, id) =>
  axios.delete(`${API_BASE}/admin/notifications/triggers/${id}`, { headers: adminHeaders(token), withCredentials: true });


export const cmsAiSuggest = (token, text, action, subject = '', topic = '') =>
  axios.post(`${API_BASE}/admin/cms/ai-suggest`, { text, action, subject, topic }, { headers: adminHeaders(token), withCredentials: true });

export const adminUpdateUserCredits = (token, userId, data) =>
  axios.patch(`${API_BASE}/admin/users/${userId}/credits`, data, { headers: adminHeaders(token), withCredentials: true });

// Task #615 — admin read/reset of the per-user daily quiz quota.
export const adminGetQuizQuota = (token, userId) =>
  axios.get(`${API_BASE}/admin/users/${userId}/quiz-quota`, { headers: adminHeaders(token), withCredentials: true });

export const adminResetQuizQuota = (token, userId) =>
  axios.post(`${API_BASE}/admin/users/${userId}/quiz-quota/reset`, {}, { headers: adminHeaders(token), withCredentials: true });

// ── Personalized CMS ────────────────────────────────────────────────────────
const cmsPersonalize = (body) =>
  apiClient().post('/cms/personalize', body);

const cmsListPlans = (userId) =>
  apiClient().get(`/cms/${userId}`);

const adminPipelineAutoGenerate = (token, subjectId, skipExisting = false) =>
  axios.post(`${API_BASE}/admin/pipeline/auto-generate`, { subject_id: subjectId, skip_existing: skipExisting }, { headers: adminHeaders(token), withCredentials: true });

const adminPipelineStatus = (token, jobId) =>
  axios.get(`${API_BASE}/admin/pipeline/status/${jobId}`, { headers: adminHeaders(token), withCredentials: true });


export const adminIntelligenceOverview = (token) =>
  axios.get(`${API_BASE}/admin/intelligence/overview`, { headers: adminHeaders(token), withCredentials: true });

export const adminContentAutoHeal = (token) =>
  axios.post(`${API_BASE}/admin/content/auto-heal`, {}, { headers: adminHeaders(token), withCredentials: true });

const adminContentVersionHistory = (token, chapterId) =>
  axios.get(`${API_BASE}/admin/content/version-history/${chapterId}`, { headers: adminHeaders(token), withCredentials: true });

export const postChatFeedback = (data) =>
  axios.post(`${API_BASE}/chat-feedback`, data, { headers: anonHeaders(), withCredentials: true });

export const adminGetChatFeedback = (token, limit = 100, offset = 0) =>
  axios.get(`${API_BASE}/chat-feedback?limit=${limit}&offset=${offset}`, { headers: adminHeaders(token), withCredentials: true });

export const adminGetFeedbackStats = (token) =>
  axios.get(`${API_BASE}/chat-feedback/stats`, { headers: adminHeaders(token), withCredentials: true });

export const adminPurgeAllCache = (token) =>
  axios.post(`${API_BASE}/admin/cache/purge-all`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminGetSpoofedBots = (token, days = 7) =>
  axios.get(`${API_BASE}/admin/security/spoofed-bots`, { headers: adminHeaders(token), withCredentials: true, params: { days } });

export const adminGetBlockedIps = (token) =>
  axios.get(`${API_BASE}/admin/security/blocked-ips`, { headers: adminHeaders(token), withCredentials: true });

export const adminGetBlockTrends = (token, days = 30) =>
  axios.get(`${API_BASE}/admin/security/block-trends`, { params: { days }, headers: adminHeaders(token), withCredentials: true });

export const adminBlockIp = (token, ip_hash, reason = 'repeat_spoof_offender', expires_in = null) =>
  axios.post(`${API_BASE}/admin/security/block-ip`, { ip_hash, reason, expires_in }, { headers: adminHeaders(token), withCredentials: true });

export const adminUnblockIp = (token, ip_hash) =>
  axios.post(`${API_BASE}/admin/security/unblock-ip`, { ip_hash }, { headers: adminHeaders(token), withCredentials: true });

export const adminGetTtlMonitor = (token) =>
  axios.get(`${API_BASE}/admin/security/ttl-monitor`, { headers: adminHeaders(token), withCredentials: true });

export const adminGetCollectionSizeHistory = (token, days = 90) =>
  axios.get(`${API_BASE}/admin/security/collection-size-history`, { params: { days }, headers: adminHeaders(token), withCredentials: true });

export const adminGetAlertSettings = (token) =>
  axios.get(`${API_BASE}/admin/alert-settings`, { headers: adminHeaders(token), withCredentials: true });

export const adminUpdateAlertSettings = (token, data) =>
  axios.put(`${API_BASE}/admin/alert-settings`, data, { headers: adminHeaders(token), withCredentials: true });

export const adminTestAlertDelivery = (token) =>
  axios.post(`${API_BASE}/admin/alert-settings/test-delivery`, {}, { headers: adminHeaders(token), withCredentials: true });

// Task #660: manually trigger the weekly review-prompt digest send (or
// preview the rendered HTML / resolved recipient list). When `to` is
// supplied, it overrides the persisted recipient list so admins can
// "send me a test now" without first saving the field.
export const adminSendReviewPromptWeeklyDigest = (token, { to = null, previewOnly = false } = {}) => {
  const params = previewOnly ? { preview_only: true } : {};
  const body = to ? { to } : {};
  return axios.post(
    `${API_BASE}/admin/analytics/review-prompt-weekly-digest/send`,
    body,
    { headers: adminHeaders(token), withCredentials: true, params },
  );
};

export const adminGetAlerts = (token, { limit = 50, acknowledged, type, date_from, date_to, include_synthetic } = {}) => {
  const params = { limit };
  if (acknowledged !== undefined && acknowledged !== null) params.acknowledged = acknowledged;
  if (type) params.type = type;
  if (date_from) params.date_from = date_from;
  if (date_to) params.date_to = date_to;
  if (include_synthetic) params.include_synthetic = true;
  return axios.get(`${API_BASE}/admin/alerts`, { headers: adminHeaders(token), withCredentials: true, params });
};

export const adminGetUnacknowledgedAlertCount = (token) =>
  axios.get(`${API_BASE}/admin/alerts/unacknowledged-count`, { headers: adminHeaders(token), withCredentials: true });

export const adminAcknowledgeAlert = (token, alertId) =>
  axios.patch(`${API_BASE}/admin/alerts/${alertId}/acknowledge`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminAcknowledgeAllAlerts = (token) =>
  axios.patch(`${API_BASE}/admin/alerts/acknowledge-all`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminBackfillThresholds = (token) =>
  axios.post(`${API_BASE}/admin/alerts/backfill-thresholds`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminGetAlertCooldowns = (token, { limit = 200, only_active } = {}) => {
  const params = { limit };
  if (only_active) params.only_active = true;
  return axios.get(`${API_BASE}/admin/alerts/cooldowns`, { headers: adminHeaders(token), withCredentials: true, params });
};

export const adminReleaseAlertCooldown = (token, dedupKey) =>
  axios.delete(`${API_BASE}/admin/alerts/cooldowns/${encodeURIComponent(dedupKey)}`, { headers: adminHeaders(token), withCredentials: true });

const adminIndexNowPing = (token, urls = []) =>
  axios.post(`${API_BASE}/admin/indexnow/ping`, { urls }, { headers: adminHeaders(token), withCredentials: true });

const adminIndexNowStatus = (token) =>
  axios.get(`${API_BASE}/admin/indexnow/status`, { headers: adminHeaders(token), withCredentials: true });

export const adminIndexNowBackfillStart = (token) =>
  axios.post(`${API_BASE}/admin/indexnow/backfill-all`, {}, { headers: adminHeaders(token), withCredentials: true });

export const adminIndexNowBackfillProgress = (token) =>
  axios.get(`${API_BASE}/admin/indexnow/backfill-progress`, { headers: adminHeaders(token), withCredentials: true });

// Task #560: Submit & Monitor — manual single/batch URL submit + recent log + sitemap ping
export const adminIndexNowSubmitUrls = (token, urls = []) =>
  axios.post(`${API_BASE}/admin/indexnow/submit-urls`, { urls }, { headers: adminHeaders(token), withCredentials: true });

export const adminIndexNowHistory = (token, limit = 20) =>
  axios.get(`${API_BASE}/admin/indexnow/history`, { headers: adminHeaders(token), withCredentials: true, params: { limit } });

export const adminSeoGoogleSitemapPing = (token) =>
  axios.post(`${API_BASE}/admin/seo/google-sitemap-ping`, {}, { headers: adminHeaders(token), withCredentials: true });

// Task #563: publish → sub-sitemap → IndexNow → push-log smoke test
export const adminSeoIndexNowSmoke = (token) =>
  axios.post(`${API_BASE}/admin/seo/indexnow/smoke`, {}, { headers: adminHeaders(token), withCredentials: true });

// Task #564: smoke run history (manual + cron) for the trend strip
export const adminSeoIndexNowSmokeHistory = (token, limit = 50) =>
  axios.get(`${API_BASE}/admin/seo/indexnow/smoke/history`, { headers: adminHeaders(token), withCredentials: true, params: { limit } });

// ─────────────────────────────────────────────────────────────────────
// Task #944 — Unified Log Explorer
// ─────────────────────────────────────────────────────────────────────
// Helper that turns the (mostly) flat filter state in AdminLogsExplorer
// into the URLSearchParams shape the backend expects. The backend route
// (admin_logs.admin_list_logs) accepts ``sources`` and ``levels`` as
// comma-separated strings, so we pre-flatten arrays here to avoid
// surprising serialisation by axios's default `paramsSerializer`.
const _logsFiltersToParams = (filters = {}) => {
  const out = {};
  if (Array.isArray(filters.sources) && filters.sources.length)
    out.sources = filters.sources.join(',');
  if (Array.isArray(filters.levels) && filters.levels.length)
    out.levels = filters.levels.join(',');
  if (filters.status_min != null && filters.status_min !== '')
    out.status_min = Number(filters.status_min);
  if (filters.status_max != null && filters.status_max !== '')
    out.status_max = Number(filters.status_max);
  if (filters.route_prefix) out.route_prefix = filters.route_prefix;
  if (filters.correlation_id) out.correlation_id = filters.correlation_id;
  if (filters.q) out.q = filters.q;
  if (filters.since) out.since = filters.since;
  if (filters.until) out.until = filters.until;
  return out;
};

export const adminLogsList = (token, { filters = {}, limit = 200, before } = {}) =>
  axios.get(`${API_BASE}/admin/logs`, {
    headers: adminHeaders(token),
    withCredentials: true,
    params: { ..._logsFiltersToParams(filters), limit, ...(before ? { before } : {}) },
  });

export const adminLogsTrace = (token, correlationId) =>
  axios.get(`${API_BASE}/admin/logs/trace/${encodeURIComponent(correlationId)}`,
    { headers: adminHeaders(token), withCredentials: true });

export const adminLogsStatus = (token) =>
  axios.get(`${API_BASE}/admin/logs/status`,
    { headers: adminHeaders(token), withCredentials: true });

export const adminLogsPause = (token) =>
  axios.post(`${API_BASE}/admin/logs/pause`, {},
    { headers: adminHeaders(token), withCredentials: true });

export const adminLogsResume = (token) =>
  axios.post(`${API_BASE}/admin/logs/resume`, {},
    { headers: adminHeaders(token), withCredentials: true });

export const adminLogsRotateToken = (token) =>
  axios.post(`${API_BASE}/admin/logs/rotate-token`, {},
    { headers: adminHeaders(token), withCredentials: true });

export const adminLogsClear = (token, sources = []) => {
  const params = sources.length ? { sources: sources.join(',') } : undefined;
  return axios.delete(`${API_BASE}/admin/logs`, {
    headers: adminHeaders(token), withCredentials: true, params,
  });
};

// Returns a fully-qualified URL (with query string) the browser can hit
// directly via window.open or <a download>. Cookies + admin JWT auth
// must already be in place. Kept for any caller that already relies
// on cookie-only admin auth.
export const adminLogsExportUrl = ({ filters = {}, fmt = 'ndjson', limit = 5000 } = {}) => {
  const sp = new URLSearchParams();
  const params = _logsFiltersToParams(filters);
  Object.entries(params).forEach(([k, v]) => sp.set(k, String(v)));
  sp.set('fmt', fmt);
  sp.set('limit', String(limit));
  return `${API_BASE}/admin/logs/export?${sp.toString()}`;
};

// Authenticated download — fetches the export as a Blob with the admin
// Bearer token in the Authorization header, then triggers a browser
// download. Use this in environments where admin auth is Bearer-only
// (no cookie). Returns the filename used for the download so callers
// can surface a confirmation toast.
export const adminLogsDownloadExport = async (token,
                                              { filters = {}, fmt = 'ndjson', limit = 5000 } = {}) => {
  const params = _logsFiltersToParams(filters);
  const resp = await axios.get(`${API_BASE}/admin/logs/export`, {
    headers: adminHeaders(token),
    withCredentials: true,
    responseType: 'blob',
    params: { ...params, fmt, limit },
  });
  const ext = fmt === 'csv' ? 'csv' : 'ndjson';
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const filename = `unified_logs_${stamp}.${ext}`;
  const url = window.URL.createObjectURL(new Blob([resp.data]));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Defer revoke so Safari has time to begin the download.
  setTimeout(() => window.URL.revokeObjectURL(url), 1000);
  return filename;
};
