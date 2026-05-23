import { useState, useEffect } from 'react';
import { Lightbulb, Loader2, Zap } from 'lucide-react';
import { toast } from 'sonner';
import {
  vertexSuggestTopics, getAllSubjects, getClasses,
  adminSeoCreateTopic,
} from '@/utils/api';
import { card, btn, Badge, readHubCtx } from './shared';

const FALLBACK_SUBJECTS = ['Physics', 'Chemistry', 'Mathematics', 'Biology', 'English', 'Accountancy', 'Business Studies', 'Economics', 'History', 'Political Science', 'Geography'];
const FALLBACK_CLASSES = ['Class 11', 'Class 12', 'Degree 1st Year', 'Degree 2nd Year', 'Degree 3rd Year'];

export default function TopicSuggesterCard({ token, onNavigate }) {
  const [subjects, setSubjects] = useState(FALLBACK_SUBJECTS);
  const [classes, setClasses] = useState(FALLBACK_CLASSES);
  const [subject, setSubject] = useState('Physics');
  const [classN, setClassN] = useState('Class 11');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [optionsError, setOptionsError] = useState(false);
  const [pushing, setPushing] = useState(false);

  const hubCtx = readHubCtx();

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      getAllSubjects(),
      getClasses(),
    ]).then(([subRes, clsRes]) => {
      if (cancelled) return;
      if (subRes.status === 'fulfilled') {
        const list = (subRes.value.data || []).map(s => s.name || s.title || s).filter(Boolean);
        if (list.length > 0) {
          setSubjects(list);
          const hubSub = hubCtx?.subjectName;
          setSubject(hubSub && list.includes(hubSub) ? hubSub : list[0]);
        }
      } else {
        setOptionsError(true);
      }
      if (clsRes.status === 'fulfilled') {
        const list = (clsRes.value.data || []).map(c => c.name || c.title || c).filter(Boolean);
        if (list.length > 0) {
          setClasses(list);
          const hubCls = hubCtx?.className;
          setClassN(hubCls && list.includes(hubCls) ? hubCls : list[0]);
        }
      }
    });
    return () => { cancelled = true; };
  }, []);

  async function run() {
    setLoading(true);
    try {
      const r = await vertexSuggestTopics(token, subject, classN);
      setResults(r.data.suggestions || []);
      toast.success(`${r.data.suggestions?.length || 0} topic suggestions ready`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Topic suggestion failed');
    } finally { setLoading(false); }
  }

  return (
    <div style={card}>
      <div className="flex items-center gap-2 mb-4">
        <Lightbulb size={16} color="#a855f7" />
        <span style={{ fontWeight: 700, color: '#111827' }}>Topic Suggester</span>
        <Badge label="Gap Analysis" color="#a855f7" />
      </div>
      <p style={{ fontSize: 12, color: '#6b7280', marginBottom: optionsError ? 8 : 12 }}>
        AI finds high-search-volume topics you haven't covered yet. Add them to your SEO pipeline.
      </p>
      {optionsError && (
        <p style={{ fontSize: 11, color: '#f59e0b', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)', borderRadius: 6, padding: '4px 10px', marginBottom: 10 }}>
          Could not load subjects from API — using defaults. Check backend connection.
        </p>
      )}
      <div className="flex gap-2 mb-4">
        <select value={subject} onChange={e => setSubject(e.target.value)}
          style={{ flex: 1, background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '8px 12px', color: '#111827', fontSize: 13 }}>
          {subjects.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={classN} onChange={e => setClassN(e.target.value)}
          style={{ background: '#e5e7eb', border: '1px solid #e5e7eb', borderRadius: 10, padding: '8px 12px', color: '#111827', fontSize: 13 }}>
          {classes.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <button onClick={run} disabled={loading} style={btn('#a855f7')}>
          {loading ? <Loader2 size={13} className="animate-spin" /> : <Lightbulb size={13} />}
          Suggest
        </button>
      </div>
      {results.length > 0 && (
        <div>
          <div style={{ maxHeight: 300, overflowY: 'auto' }}>
            {results.map((r, i) => (
              <div key={i} style={{ padding: '10px 14px', borderBottom: '1px solid #f3f4f6', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                <div style={{ marginTop: 2 }}>
                  <Badge label={r.priority || 'medium'} color={r.priority === 'high' ? '#ef4444' : '#f59e0b'} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{r.title}</div>
                  <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>{r.reason}</div>
                </div>
                <div style={{ textAlign: 'right', flexShrink: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#a855f7' }}>~{r.search_volume_estimate?.toLocaleString()}</div>
                  <div style={{ fontSize: 10, color: '#9ca3af' }}>searches/mo</div>
                </div>
              </div>
            ))}
          </div>
          {onNavigate && (
            <div style={{ display: 'flex', gap: 8, marginTop: 12, paddingTop: 12, borderTop: '1px solid #e5e7eb' }}>
              <button
                onClick={async () => {
                  if (!token) { toast.error('Not authenticated'); return; }
                  setPushing(true);
                  toast.loading('Pushing topics to SEO pipeline…', { id: 'push-seo' });
                  try {
                    let pushed = 0;
                    for (const r of results) {
                      await adminSeoCreateTopic(token, {
                        title:      r.title,
                        slug:       r.title.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
                        subject_id: hubCtx?.subjectId || '',
                        chapter_id: '',
                        definition: r.reason || '',
                        status:     'published',
                      });
                      pushed++;
                    }
                    toast.success(`Pushed ${pushed} topics to SEO pipeline`, { id: 'push-seo' });
                    onNavigate('seomanager');
                  } catch (e) {
                    toast.error(e.response?.data?.detail || 'Push failed', { id: 'push-seo' });
                  } finally { setPushing(false); }
                }}
                disabled={pushing}
                style={{ ...btn('#a855f7'), fontSize: 12 }}>
                {pushing ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
                Push {results.length} topics to SEO
              </button>
              <button
                onClick={() => onNavigate('seomanager')}
                style={{ background: 'rgba(168,85,247,0.10)', border: '1px solid rgba(168,85,247,0.25)', color: '#d8b4fe', borderRadius: 8, padding: '7px 14px', fontSize: 12, fontWeight: 700, cursor: 'pointer' }}>
                Go to SEO Manager →
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
