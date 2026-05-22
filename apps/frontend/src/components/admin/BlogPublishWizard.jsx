import { useState, useEffect, useCallback } from 'react';
import { Globe, Loader2, RefreshCw, CheckCircle2, BookOpen, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';
import { API, authHeaders } from '@/utils/adminHelpers';

const selectClass = "h-10 rounded-xl bg-gray-50 border border-gray-200 text-sm text-gray-900 px-3 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20";

export default function BlogPublishWizard({ adminToken, hubContext, onHubContext }) {
  const [boards, setBoards] = useState([]);
  const [classes, setClasses] = useState([]);
  const [streams, setStreams] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [selBoard, setSelBoard] = useState(hubContext?.boardId || '');
  const [selClass, setSelClass] = useState(hubContext?.classId || '');
  const [selStream, setSelStream] = useState(hubContext?.streamId || '');
  const [selSubject, setSelSubject] = useState(hubContext?.subjectId || '');
  const [publishing, setPublishing] = useState(false);
  const [lastResult, setLastResult] = useState(null);

  useEffect(() => {
    Promise.all([
      axios.get(`${API}/content/boards`),
      axios.get(`${API}/content/classes`),
      axios.get(`${API}/content/streams`),
      axios.get(`${API}/content/subjects`),
    ]).then(([b, c, s, sub]) => {
      setBoards(b.data || []);
      setClasses(c.data || []);
      setStreams(s.data || []);
      setSubjects(sub.data || []);
    }).catch(() => toast.error('Failed to load content hierarchy'));
  }, []);

  useEffect(() => {
    if (hubContext?.subjectId && !selSubject) {
      setSelBoard(hubContext.boardId || '');
      setSelClass(hubContext.classId || '');
      setSelStream(hubContext.streamId || '');
      setSelSubject(hubContext.subjectId);
    }
  }, [hubContext?.subjectId]);

  const filteredClasses = selBoard ? classes.filter(c => c.board_id === selBoard) : [];
  const filteredStreams = selClass ? streams.filter(s => s.class_id === selClass) : [];
  const filteredSubjects = selStream ? subjects.filter(s => s.stream_id === selStream) : [];
  const selectedSubject = subjects.find(s => s.id === selSubject);

  const handlePublish = useCallback(async () => {
    if (!selSubject) return toast.error('Select a subject first');
    setPublishing(true);
    setLastResult(null);
    try {
      const res = await axios.post(`${API}/admin/cms/merge/${selSubject}`, {}, authHeaders(adminToken));
      setLastResult({ success: true, data: res.data });
      toast.success(`Published "${selectedSubject?.name || 'Subject'}" — blog view ready`);
    } catch (e) {
      const detail = e.response?.data?.detail || 'Publish failed';
      setLastResult({ success: false, error: detail });
      toast.error(detail);
    } finally {
      setPublishing(false);
    }
  }, [selSubject, adminToken, selectedSubject?.name]);

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6">
      <div>
        <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
          <Globe size={18} className="text-violet-500" /> One-Click Publish
        </h2>
        <p className="text-sm text-gray-400 mt-1">
          Select a subject and publish all chapters as a merged blog post.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <select
          value={selBoard}
          onChange={e => { setSelBoard(e.target.value); setSelClass(''); setSelStream(''); setSelSubject(''); setLastResult(null); }}
          className={selectClass}
        >
          <option value="">Board</option>
          {boards.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <select
          value={selClass}
          onChange={e => { setSelClass(e.target.value); setSelStream(''); setSelSubject(''); setLastResult(null); }}
          className={selectClass}
        >
          <option value="">Class</option>
          {filteredClasses.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <select
          value={selStream}
          onChange={e => { setSelStream(e.target.value); setSelSubject(''); setLastResult(null); }}
          className={selectClass}
        >
          <option value="">Stream</option>
          {filteredStreams.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select
          value={selSubject}
          onChange={e => { setSelSubject(e.target.value); setLastResult(null); }}
          className={selectClass}
        >
          <option value="">Subject</option>
          {filteredSubjects.map(s => <option key={s.id} value={s.id}>{s.icon} {s.name}</option>)}
        </select>
      </div>

      {selSubject && selectedSubject && (
        <div className="rounded-xl border border-gray-200 p-4 space-y-3 bg-white shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-violet-50 flex items-center justify-center text-lg">
              {selectedSubject.icon || '📚'}
            </div>
            <div>
              <p className="text-sm font-medium text-gray-900">{selectedSubject.name}</p>
              <p className="text-xs text-gray-400">{selectedSubject.description || 'No description'}</p>
            </div>
          </div>

          <button
            onClick={handlePublish}
            disabled={publishing}
            className="w-full h-11 rounded-xl text-sm font-semibold text-white flex items-center justify-center gap-2 transition-all hover:opacity-90 active:scale-[0.98] disabled:opacity-50 bg-violet-600 hover:bg-violet-700"
          >
            {publishing ? (
              <><Loader2 size={14} className="animate-spin" /> Publishing...</>
            ) : (
              <><Globe size={14} /> Publish Now</>
            )}
          </button>
        </div>
      )}

      {lastResult && (
        <div
          className="rounded-xl border p-4"
          style={{
            background: lastResult.success ? '#ecfdf5' : '#fef2f2',
            borderColor: lastResult.success ? '#a7f3d0' : '#fecaca',
          }}
        >
          <div className="flex items-center gap-2">
            {lastResult.success ? (
              <CheckCircle2 size={16} className="text-emerald-500" />
            ) : (
              <RefreshCw size={16} className="text-red-500" />
            )}
            <p className="text-sm font-medium" style={{ color: lastResult.success ? '#059669' : '#dc2626' }}>
              {lastResult.success ? 'Published successfully' : 'Publish failed'}
            </p>
          </div>
          {lastResult.success && lastResult.data?.word_count && (
            <p className="text-xs text-gray-500 mt-1">
              {lastResult.data.word_count.toLocaleString()} words merged
            </p>
          )}
          {lastResult.error && (
            <p className="text-xs text-red-500 mt-1">{lastResult.error}</p>
          )}
        </div>
      )}
    </div>
  );
}
