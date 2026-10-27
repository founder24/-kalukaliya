import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Check, CircleHelp, Loader2, Plus, Save, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';
import { API, authHeaders } from '@/utils/adminHelpers';
import { canStaffCapability } from '@/utils/staffAccess';

export default function TopicAnswerManager({ chapter, adminToken, user, onBack }) {
  const [topics, setTopics] = useState([]);
  const [questions, setQuestions] = useState({ en: [], as: [] });
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState(null);
  const [title, setTitle] = useState('');
  const [answer, setAnswer] = useState('');
  const canEdit = canStaffCapability(user, 'content:edit');
  const canPublish = canStaffCapability(user, 'content:publish');
  const canDelete = canStaffCapability(user, 'content:delete');

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const response = await axios.get(
        `${API}/staff/content/chapter/${chapter.id}/topics`,
        authHeaders(adminToken),
      );
      setTopics(Array.isArray(response.data?.topics) ? response.data.topics : []);
      setQuestions(response.data?.questions || { en: [], as: [] });
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Question cards could not be loaded');
    } finally {
      if (!quiet) setLoading(false);
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
            <TopicPageEditor
              key={topic.id}
              topic={topic}
              questions={questions}
              chapterId={chapter.id}
              adminToken={adminToken}
              canEdit={canEdit}
              canPublish={canPublish}
              onSaved={() => load(true)}
            />
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

function linkedToTopic(question, topic, language) {
  const links = value => Array.isArray(value) ? value : value ? [value] : [];
  const id = topic.id;
  const slug = language === 'as' ? topic.topic_slug_as : topic.topic_slug;
  return links(question.topic_id).includes(id) ||
    links(question.linked_topic_id).includes(id) ||
    links(question.topic_ids).includes(id) ||
    (slug && (links(question.topic_slug).includes(slug) ||
      links(question.linked_topic_slug).includes(slug) ||
      links(question.topic_slugs).includes(slug)));
}

function TopicPageEditor({ topic, questions, chapterId, adminToken, canEdit, canPublish, onSaved }) {
  const [notesEn, setNotesEn] = useState(topic.notes_en || '');
  const [notesAs, setNotesAs] = useState(topic.notes_as || '');
  const [selectedEn, setSelectedEn] = useState(() => questions.en.filter(q => q?.question && linkedToTopic(q, topic, 'en')).map(q => q.editor_question_id));
  const [selectedAs, setSelectedAs] = useState(() => questions.as.filter(q => q?.question && linkedToTopic(q, topic, 'as')).map(q => q.editor_question_id));
  const [statusEn, setStatusEn] = useState(topic.notes_status_en || 'draft');
  const [statusAs, setStatusAs] = useState(topic.notes_status_as || 'draft');
  const [busy, setBusy] = useState('');
  const headers = authHeaders(adminToken);
  const route = `${API}/staff/content/chapter/${chapterId}/topics/${topic.id}`;

  const saveNotes = async language => {
    setBusy(`notes-${language}`);
    try {
      await axios.patch(route, { [language === 'en' ? 'notes_en' : 'notes_as']: language === 'en' ? notesEn : notesAs }, headers);
      toast.success(`${language === 'en' ? 'English' : 'Assamese'} topic notes saved`);
      await onSaved();
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Topic notes could not be saved');
    } finally { setBusy(''); }
  };

  const saveLinks = async language => {
    setBusy(`links-${language}`);
    try {
      await axios.patch(`${route}/question-links/${language}`, { question_ids: language === 'en' ? selectedEn : selectedAs }, headers);
      toast.success(`${language === 'en' ? 'English' : 'Assamese'} question links saved`);
      await onSaved();
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Question links could not be saved');
    } finally { setBusy(''); }
  };

  const approve = async language => {
    const isApproved = language === 'en' ? statusEn === 'approved' : statusAs === 'approved';
    const action = `${isApproved ? 'unapprove' : 'approve'}-${language}`;
    setBusy(action);
    try {
      const response = await axios.post(`${route}/${action}`, {}, headers);
      const next = response.data?.topic?.[`notes_status_${language}`] || (isApproved ? 'draft' : 'approved');
      language === 'en' ? setStatusEn(next) : setStatusAs(next);
      await onSaved();
      toast.success(`${language === 'en' ? 'English' : 'Assamese'} topic page ${next}`);
    } catch (error) {
      toast.error(error?.response?.data?.detail || 'Topic page approval could not be changed');
    } finally { setBusy(''); }
  };

  const languageEditor = (language, notes, setNotes, selected, setSelected, status) => {
    const list = questions[language] || [];
    const validQuestions = list.filter(q => q && typeof q === 'object' &&
      typeof q.question === 'string' && q.question.trim() &&
      ((typeof q.answer === 'string' && q.answer.trim()) || (typeof q.solution === 'string' && q.solution.trim())) &&
      q.editor_question_id);
    return (
      <section className="mt-4 rounded-lg border border-gray-200 p-3" data-testid={`topic-page-${language}-${topic.id}`}>
        <div className="flex items-center justify-between gap-2">
          <h4 className="text-sm font-semibold text-gray-800">{language === 'en' ? 'English topic page' : 'Assamese topic page'}</h4>
          <span className={`text-xs font-medium ${status === 'approved' ? 'text-emerald-700' : 'text-amber-700'}`}>{status}</span>
        </div>
        <label className="mt-3 block text-xs font-medium text-gray-600">Topic notes</label>
        <textarea value={notes} onChange={event => setNotes(event.target.value)} rows={5} disabled={!canEdit || !!busy}
          className="mt-1 w-full rounded-lg border border-gray-200 p-3 text-sm leading-relaxed disabled:bg-gray-50"
          placeholder={language === 'en' ? 'Write the approved English topic notes…' : 'অসমীয়া বিষয়ভিত্তিক টোকা লিখক…'}
          data-testid={`topic-page-notes-${language}-${topic.id}`} />
        <p className="mt-2 text-xs font-medium text-gray-600">Link existing chapter questions (question and answer/solution required)</p>
        {validQuestions.length ? (
          <div className="mt-1 max-h-40 space-y-1 overflow-y-auto rounded-md border border-gray-100 p-2">
            {validQuestions.map(question => (
              <label key={question.editor_question_id} className="flex items-start gap-2 py-1 text-xs text-gray-700">
                <input type="checkbox" disabled={!canEdit || !!busy} checked={selected.includes(question.editor_question_id)}
                  onChange={event => setSelected(current => event.target.checked
                    ? [...new Set([...current, question.editor_question_id])]
                    : current.filter(id => id !== question.editor_question_id))} />
                <span>{question.question}</span>
              </label>
            ))}
          </div>
        ) : <p className="mt-1 rounded-md bg-amber-50 p-2 text-xs text-amber-800">No structured question-and-solution entries are available in this language.</p>}
        <div className="mt-3 flex flex-wrap justify-end gap-2">
          <button type="button" disabled={!canEdit || !!busy} onClick={() => saveNotes(language)}
            className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs disabled:opacity-40">
            <Save size={13} /> Save notes
          </button>
          <button type="button" disabled={!canEdit || !!busy} onClick={() => saveLinks(language)}
            className="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs disabled:opacity-40">
            <Save size={13} /> Save question links
          </button>
          <button type="button" disabled={!canPublish || !!busy} onClick={() => approve(language)}
            className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
            <Check size={13} /> {status === 'approved' ? 'Unapprove' : 'Approve'} {language === 'en' ? 'English' : 'Assamese'}
          </button>
        </div>
      </section>
    );
  };

  return (
    <div className="mt-4 border-t border-gray-100 pt-1">
      {languageEditor('en', notesEn, setNotesEn, selectedEn, setSelectedEn, statusEn)}
      {languageEditor('as', notesAs, setNotesAs, selectedAs, setSelectedAs, statusAs)}
    </div>
  );
}
