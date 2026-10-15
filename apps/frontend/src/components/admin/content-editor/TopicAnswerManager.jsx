import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, CircleHelp, Loader2, Plus, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';
import { API, authHeaders } from '@/utils/adminHelpers';
import { canStaffCapability } from '@/utils/staffAccess';

export default function TopicAnswerManager({ chapter, adminToken, user, onBack }) {
  const [topics, setTopics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [title, setTitle] = useState('');
  const [answer, setAnswer] = useState('');
  const canEdit = canStaffCapability(user, 'content:edit');
  const canPublish = canStaffCapability(user, 'content:publish');
  const canDelete = canStaffCapability(user, 'content:delete');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await axios.get(
        `${API}/staff/content/chapter/${chapter.id}/topics`,
        authHeaders(adminToken),
      );
      setTopics(Array.isArray(response.data?.topics) ? response.data.topics : []);
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Question cards could not be loaded');
    } finally {
      setLoading(false);
    }
  }, [adminToken, chapter.id]);

  useEffect(() => { load(); }, [load]);

  const createTopic = async () => {
    if (!title.trim() || !answer.trim() || !canEdit) return;
    setBusyId('create');
    try {
      await axios.post(
        `${API}/staff/content/chapter/${chapter.id}/topics`,
        { title: title.trim(), content: answer.trim() },
        authHeaders(adminToken),
      );
      setTitle('');
      setAnswer('');
      await load();
      toast.success('Question card saved as a draft');
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Question card could not be saved');
    } finally {
      setBusyId(null);
    }
  };

  const setPublished = async (topic) => {
    if (!canPublish) return;
    setBusyId(topic.id);
    try {
      const action = topic.status === 'published' ? 'unpublish' : 'publish';
      await axios.post(
        `${API}/staff/content/chapter/${chapter.id}/topics/${topic.id}/${action}`,
        {},
        authHeaders(adminToken),
      );
      await load();
      toast.success(action === 'publish' ? 'Question card published' : 'Question card unpublished');
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Question card status could not be changed');
    } finally {
      setBusyId(null);
    }
  };

  const deleteTopic = async (topic) => {
    if (!canDelete || !window.confirm(`Delete question card "${topic.title}"?`)) return;
    setBusyId(topic.id);
    try {
      await axios.delete(
        `${API}/staff/content/chapter/${chapter.id}/topics/${topic.id}`,
        authHeaders(adminToken),
      );
      await load();
      toast.success('Question card deleted');
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Question card could not be deleted');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="flex h-full min-h-0 flex-col bg-white" data-testid="topic-answer-manager">
      <header className="shrink-0 border-b border-gray-200 px-5 py-4 sm:px-6">
        <button type="button" onClick={onBack} className="mb-3 inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-900">
          <ArrowLeft size={15} /> Back to chapters
        </button>
        <div className="flex items-center gap-3">
          <CircleHelp className="text-violet-600" size={20} />
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Question answer cards</h2>
            <p className="mt-1 text-xs text-gray-500">{chapter.title}</p>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto space-y-4 px-5 py-5 sm:px-6">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 size={16} className="animate-spin" />Loading cards…</div>
        ) : topics.length ? topics.map(topic => (
          <article key={topic.id} className="rounded-xl border border-gray-200 p-4" data-testid={`topic-answer-row-${topic.id}`}>
            <div className="flex items-start justify-between gap-3">
              <h3 className="font-semibold text-sm text-gray-900">{topic.title}</h3>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${topic.status === 'published' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                {topic.status || 'draft'}
              </span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-gray-700">{topic.content || topic.definition || ''}</p>
            <div className="mt-3 flex gap-2">
              <button type="button" disabled={!canPublish || busyId === topic.id} onClick={() => setPublished(topic)} className="rounded-lg border px-3 py-1.5 text-xs disabled:opacity-40">
                {busyId === topic.id ? 'Working…' : topic.status === 'published' ? 'Unpublish' : 'Publish'}
              </button>
              <button type="button" disabled={!canDelete || busyId === topic.id} onClick={() => deleteTopic(topic)} className="inline-flex items-center gap-1 rounded-lg border border-rose-200 px-3 py-1.5 text-xs text-rose-700 disabled:opacity-40">
                <Trash2 size={12} /> Delete
              </button>
            </div>
          </article>
        )) : (
          <p className="rounded-xl border border-dashed border-gray-300 px-4 py-6 text-center text-sm text-gray-500">No answer cards on this chapter yet.</p>
        )}

        <div className="rounded-xl border border-violet-100 bg-violet-50/40 p-4">
          <h3 className="font-semibold text-sm text-gray-900">Add an answer card</h3>
          <p className="mt-1 text-xs text-gray-500">Include source titles and URLs in the answer. New cards start as drafts.</p>
          <input
            value={title}
            onChange={event => setTitle(event.target.value)}
            disabled={!canEdit || busyId === 'create'}
            placeholder="Question or topic title"
            className="mt-3 h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-sm disabled:bg-gray-100"
            data-testid="topic-answer-title"
          />
          <textarea
            value={answer}
            onChange={event => setAnswer(event.target.value)}
            disabled={!canEdit || busyId === 'create'}
            placeholder="Answer with citations (source titles and URLs)"
            rows={6}
            className="mt-2 w-full rounded-lg border border-gray-200 bg-white p-3 text-sm leading-relaxed disabled:bg-gray-100"
            data-testid="topic-answer-content"
          />
          <div className="mt-3 flex justify-end">
            <button
              type="button"
              onClick={createTopic}
              disabled={!canEdit || busyId === 'create' || !title.trim() || !answer.trim()}
              className="inline-flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
              data-testid="topic-answer-create"
            >
              {busyId === 'create' ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
              Save draft
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
