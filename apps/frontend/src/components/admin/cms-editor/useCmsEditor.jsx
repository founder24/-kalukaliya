import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import axios from 'axios';
import { cmsAiSuggest } from '@/utils/api';
import { API, authHeaders, autoSlug } from '@/utils/adminHelpers';

const EMPTY_DOC = {
  title: '', content: '', meta_description: '', description: '',
  seo_tags: '', primary_keyword: '', seo_slug: '', category: '',
  geo_tags: '', schema_type: 'Article', status: 'draft',
  thumbnail_url: '', alt_text: '',
};

export default function useCmsEditor(adminToken, onNavigate, hubContext) {
  const [docs, setDocs]               = useState([]);
  const [loading, setLoading]         = useState(true);
  const [editDoc, setEditDoc]         = useState(null);
  const [isNewDoc, setIsNewDoc]       = useState(false);
  const [form, setForm]               = useState(EMPTY_DOC);
  const [saving, setSaving]           = useState(false);
  const [publishing, setPublishing]   = useState(false);
  const [pdfLoading, setPdfLoading]   = useState(false);
  const [aiParsing, setAiParsing]     = useState(false);
  const [seoGenerating, setSeoGenerating] = useState(false);
  const [seoResult, setSeoResult]     = useState(null);
  const [searchQ, setSearchQ]         = useState('');
  const [seoTab, setSeoTab]           = useState('content');
  const [filterType, setFilterType]   = useState('all');

  const [showPreview, setShowPreview]             = useState(false);
  const [syllabusOpen, setSyllabusOpen]           = useState(false);
  const [spBoards, setSpBoards]                   = useState([]);
  const [spClasses, setSpClasses]                 = useState([]);
  const [spStreams, setSpStreams]                  = useState([]);
  const [spSubjects, setSpSubjects]               = useState([]);
  const [spBoard, setSpBoard]                     = useState('');
  const [spClass, setSpClass]                     = useState('');
  const [spStream, setSpStream]                   = useState('');
  const [spSubject, setSpSubject]                 = useState('');
  const [syllabusInserting, setSyllabusInserting] = useState(false);
  const [savingRevision, setSavingRevision]       = useState(false);
  const [linkingScope, setLinkingScope]           = useState(false);
  const [linkedScopeLabel, setLinkedScopeLabel]   = useState('');
  const [scopePickerOpen, setScopePickerOpen]     = useState(false);
  const [translateOpen, setTranslateOpen]         = useState(false);
  const [translateLang, setTranslateLang]         = useState('as');
  const [translating, setTranslating]             = useState(false);
  const [translateResult, setTranslateResult]     = useState('');
  const [aiPaletteOpen, setAiPaletteOpen]         = useState(false);
  const [aiPaletteText, setAiPaletteText]         = useState('');
  const [aiPaletteAction, setAiPaletteAction]     = useState('improve');
  const [aiPaletteResult, setAiPaletteResult]     = useState('');
  const [aiPaletteLoading, setAiPaletteLoading]   = useState(false);

  const pdfRef    = useRef(null);
  const editorRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/admin/content/cms-documents`, authHeaders(adminToken));
      setDocs(res.data || []);
    } catch { toast.error('Failed to load CMS documents'); }
    finally { setLoading(false); }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem('syrabit_cms_prefill');
      if (!raw) return;
      const prefill = JSON.parse(raw);
      if (Date.now() - (prefill.timestamp || 0) > 10 * 60 * 1000) {
        localStorage.removeItem('syrabit_cms_prefill');
        return;
      }
      localStorage.removeItem('syrabit_cms_prefill');
      setEditDoc(null);
      setForm(f => ({
        ...f,
        title:            prefill.title            || f.title,
        content:          prefill.content          || f.content,
        seo_slug:         prefill.seo_slug         || f.seo_slug,
        meta_description: prefill.meta_description || f.meta_description,
        status:           'draft',
      }));
      setSeoTab('content');
      toast.success(`Pre-filled with merged content for "${prefill.title}" — review and save`);
    } catch (err) {
      console.warn('useCmsEditor: prefill apply failed:', err);
    }
  }, []);

  useEffect(() => {
    axios.get(`${API}/content/boards`).then(r => setSpBoards(r.data || [])).catch(() => {});
  }, []);

  useEffect(() => {
    if (!hubContext?.subjectId) return;
    if (hubContext.boardId   && !spBoard)   setSpBoard(hubContext.boardId);
    if (hubContext.classId   && !spClass)   setSpClass(hubContext.classId);
    if (hubContext.streamId  && !spStream)  setSpStream(hubContext.streamId);
    if (hubContext.subjectId && !spSubject) setSpSubject(hubContext.subjectId);
  }, [hubContext?.subjectId]);

  useEffect(() => {
    if (!spBoard) { setSpClasses([]); setSpClass(''); return; }
    axios.get(`${API}/content/classes?board_id=${spBoard}`).then(r => setSpClasses(r.data || [])).catch(() => {});
    setSpClass(''); setSpStream(''); setSpSubject('');
  }, [spBoard]);

  useEffect(() => {
    if (!spClass) { setSpStreams([]); setSpStream(''); return; }
    axios.get(`${API}/content/streams?class_id=${spClass}`).then(r => setSpStreams(r.data || [])).catch(() => {});
    setSpStream(''); setSpSubject('');
  }, [spClass]);

  useEffect(() => {
    if (!spStream) { setSpSubjects([]); setSpSubject(''); return; }
    axios.get(`${API}/content/subjects?stream_id=${spStream}`).then(r => setSpSubjects(r.data || [])).catch(() => {});
    setSpSubject('');
  }, [spStream]);

  useEffect(() => {
    if (editDoc?.linked_scope) {
      const parts = editDoc.linked_scope.split('/');
      setLinkedScopeLabel(editDoc.linked_scope);
      if (parts[0]) setSpBoard(parts[0]);
      if (parts[1]) setSpClass(parts[1]);
      if (parts[2]) setSpStream(parts[2]);
      if (parts[3]) setSpSubject(parts[3]);
    } else {
      setLinkedScopeLabel('');
    }
  }, [editDoc]);

  const openNew = () => { setEditDoc(null); setIsNewDoc(true); setForm({ ...EMPTY_DOC }); setSeoTab('content'); setLinkedScopeLabel(''); setShowPreview(false); };

  const openEdit = (doc) => {
    setEditDoc(doc);
    setIsNewDoc(false);
    setForm({
      title: doc.title || '', content: doc.content || '',
      meta_description: doc.meta_description || '', description: doc.description || '',
      seo_tags: doc.seo_tags || '', primary_keyword: doc.primary_keyword || '',
      seo_slug: doc.seo_slug || '', category: doc.category || '',
      geo_tags: doc.geo_tags || '', schema_type: doc.schema_type || 'Article',
      status: doc.status || 'draft', thumbnail_url: doc.thumbnail_url || '',
      alt_text: doc.alt_text || '',
    });
    setSeoTab('content');
    setShowPreview(false);
  };

  const handleTitleChange = (title) => {
    setForm(f => ({
      ...f, title,
      seo_slug: f.seo_slug === autoSlug(f.title) || !f.seo_slug ? autoSlug(title) : f.seo_slug,
    }));
  };

  const handleAiParse = async () => {
    const content = editorRef.current?.getMarkdown() || form.content;
    if (!content.trim()) { toast.error('Add content first'); return; }
    setAiParsing(true);
    try {
      const res = await axios.post(`${API}/admin/studio/parse`, {
        raw_text: content, subject: form.geo_tags || '', chapter: form.title || '',
      }, authHeaders(adminToken));
      const blocks = res.data.blocks || [];
      if (!blocks.length) { toast.error('AI could not parse content'); return; }
      const formatted = blocks.map(b => `## ${b.title}\n\n${b.content}`).join('\n\n---\n\n');
      setForm(f => ({ ...f, content: formatted }));
      toast.success(`AI structured ${blocks.length} blocks`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'AI parsing failed');
    } finally { setAiParsing(false); }
  };

  const handleSave = async () => {
    if (!form.title.trim()) { toast.error('Title is required'); return; }
    const liveContent = editorRef.current?.getMarkdown() ?? form.content;
    setSaving(true);
    try {
      const payload = { ...form, content: liveContent, seo_slug: form.seo_slug || autoSlug(form.title) };
      if (editDoc) {
        await axios.patch(`${API}/admin/content/cms-documents/${editDoc.id}`, payload, authHeaders(adminToken));
        toast.success('Document updated');
      } else {
        const res = await axios.post(`${API}/admin/content/cms-documents`, payload, authHeaders(adminToken));
        setEditDoc(res.data);
        setIsNewDoc(false);
        toast.success('Document created');
      }
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Save failed');
    } finally { setSaving(false); }
  };

  const handlePublishToggle = async () => {
    if (!editDoc) { toast.error('Save the document first'); return; }
    setPublishing(true);
    try {
      const res = await axios.post(`${API}/admin/content/cms-documents/${editDoc.id}/publish`, {}, authHeaders(adminToken));
      const newStatus = res.data.status;
      setForm(f => ({ ...f, status: newStatus }));
      setEditDoc(d => ({ ...d, status: newStatus }));
      toast.success(newStatus === 'published' ? 'Published!' : 'Moved to draft');
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to toggle publish');
    } finally { setPublishing(false); }
  };

  const handleDelete = async (id, e) => {
    e.stopPropagation();
    if (!confirm('Delete this document permanently?')) return;
    try {
      await axios.delete(`${API}/admin/content/cms-documents/${id}`, authHeaders(adminToken));
      if (editDoc?.id === id) { setEditDoc(null); setForm(EMPTY_DOC); }
      await load();
      toast.success('Document deleted');
    } catch { toast.error('Delete failed'); }
  };

  const handlePdfUpload = async () => {
    const file = pdfRef.current?.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) { toast.error('Only PDF files accepted'); return; }
    setPdfLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await axios.post(
        `${API}/admin/content/extract-pdf-text`, formData,
        { ...authHeaders(adminToken), headers: { ...authHeaders(adminToken).headers, 'Content-Type': 'multipart/form-data' } }
      );
      const extracted = res.data.text || '';
      if (!extracted) { toast.error('No text extracted from PDF'); return; }
      const current = editorRef.current?.getMarkdown() || form.content;
      setForm(f => ({ ...f, content: current ? `${current}\n\n---\n\n${extracted}` : extracted }));
      toast.success(`Extracted ${res.data.chars?.toLocaleString() || '?'} chars from ${res.data.pages} pages`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'PDF extraction failed');
    } finally {
      setPdfLoading(false);
      if (pdfRef.current) pdfRef.current.value = '';
    }
  };

  const handleTranslate = async () => {
    const content = editorRef.current?.getMarkdown() || form.content;
    if (!content) { toast.error('No content to translate'); return; }
    setTranslating(true);
    setTranslateResult('');
    try {
      const res = await axios.post(
        `${API}/admin/vertex/translate`,
        { text: content.slice(0, 4000), target_lang: translateLang, source_lang: 'en' },
        authHeaders(adminToken)
      );
      setTranslateResult(res.data.translated || '');
      toast.success('Translation ready');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Translation failed');
    } finally { setTranslating(false); }
  };

  const handleAiPalette = async () => {
    const selected = aiPaletteText.trim();
    if (!selected) { toast.error('Enter or paste text to rewrite'); return; }
    setAiPaletteLoading(true);
    setAiPaletteResult('');
    try {
      const res = await cmsAiSuggest(adminToken, selected, aiPaletteAction, form.geo_tags || '', form.title || '');
      const suggestion = res.data?.suggestion || res.data?.text || '';
      setAiPaletteResult(suggestion);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'AI suggestion failed');
    } finally { setAiPaletteLoading(false); }
  };

  const applyAiPaletteResult = () => {
    if (!aiPaletteResult) return;
    const current = editorRef.current?.getMarkdown() || form.content;
    const updated = aiPaletteText
      ? current.replace(aiPaletteText, aiPaletteResult)
      : current + '\n\n' + aiPaletteResult;
    setForm(f => ({ ...f, content: updated }));
    setAiPaletteOpen(false);
    setAiPaletteText('');
    setAiPaletteResult('');
    toast.success('AI suggestion applied to content');
  };

  const handleInsertSyllabus = async () => {
    if (!spBoard || !spClass) { toast.error('Select at least Board and Class'); return; }
    setSyllabusInserting(true);
    try {
      let url = `${API}/syllabi/${spBoard}/${spClass}`;
      if (spStream && spSubject) url = `${API}/syllabi/${spBoard}/${spClass}/${spStream}/${spSubject}`;
      else if (spStream) url = `${API}/syllabi/${spBoard}/${spClass}/${spStream}`;
      const res = await axios.get(url, { withCredentials: true });
      const syl = res.data;
      if (!syl?.content && !syl?.chapters?.length) { toast.error('No syllabus found for this scope'); return; }
      let block = `\n\n# Subject Syllabus\n\n`;
      if (syl.content) block += `${syl.content}\n\n`;
      if (syl.topics?.length) block += `## Key Topics\n\n${syl.topics.map(t => `- ${t}`).join('\n')}\n\n`;
      if (syl.chapters?.length) block += `## Chapters\n\n${syl.chapters.map((c, i) => `${i + 1}. ${c}`).join('\n')}\n\n`;
      if (syl.guidelines) block += `## Guidelines\n\n${syl.guidelines}\n\n`;
      block += '---\n\n';
      const current = editorRef.current?.getMarkdown() || form.content;
      setForm(f => ({ ...f, content: current + block }));
      if (syl.topics?.length && !form.primary_keyword) {
        setForm(f => ({ ...f, primary_keyword: syl.topics[0] }));
      }
      setSyllabusOpen(false);
      toast.success('Syllabus block inserted');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to fetch syllabus');
    } finally { setSyllabusInserting(false); }
  };

  const handleSaveRevision = async () => {
    if (!editDoc) { toast.error('Save the document first'); return; }
    setSavingRevision(true);
    try {
      const res = await axios.post(`${API}/admin/content/cms-documents/${editDoc.id}/revisions`, {}, authHeaders(adminToken));
      await load();
      toast.success(`Revision saved: ${res.data.title}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Revision failed');
    } finally { setSavingRevision(false); }
  };

  const handleHandOff = () => {
    const liveContent = editorRef.current?.getMarkdown() || form.content;
    if (!liveContent.trim()) { toast.error('No content to hand off'); return; }
    localStorage.setItem('syrabit_editor_prefill', JSON.stringify({
      title: form.title, content: liveContent, timestamp: Date.now(),
    }));
    toast.success('Content handed off to Content Editor');
    onNavigate?.('editor');
  };

  const handleLinkSyllabus = async () => {
    if (!editDoc) { toast.error('Save the document first'); return; }
    if (!spBoard || !spClass) { toast.error('Select Board and Class at minimum'); return; }
    setLinkingScope(true);
    try {
      const res = await axios.post(
        `${API}/admin/content/cms-documents/${editDoc.id}/link-syllabus`,
        { board_id: spBoard, class_id: spClass, stream_id: spStream, subject_id: spSubject },
        authHeaders(adminToken)
      );
      setForm(f => ({ ...f, geo_tags: res.data.geo_tags || f.geo_tags }));
      setLinkedScopeLabel(`${res.data.board_name} / ${res.data.class_name}${res.data.stream_name ? ' / ' + res.data.stream_name : ''}${res.data.subject_name ? ' / ' + res.data.subject_name : ''}`);
      setEditDoc(d => ({ ...d, linked_scope: `${spBoard}/${spClass}/${spStream}/${spSubject}`, canonical_url: res.data.canonical_url }));
      setScopePickerOpen(false);
      await load();
      toast.success('Linked to syllabus scope');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Link failed');
    } finally { setLinkingScope(false); }
  };

  const handleAutoKeyword = () => {
    const terms = [form.title, ...(form.seo_tags || '').split(',').map(t => t.trim())].filter(Boolean);
    if (!terms.length) { toast.error('Add a title or SEO tags first'); return; }
    setForm(f => ({ ...f, primary_keyword: terms[0] }));
    toast.success(`Primary keyword set to "${terms[0]}"`);
  };

  const handleGenerateSeoMeta = async () => {
    if (!form.title && !form.content && !form.primary_keyword) {
      toast.error('Add a title or content first so AI has context');
      return;
    }
    setSeoGenerating(true);
    setSeoResult(null);
    try {
      const payload = {
        title: form.title, content: form.content?.slice(0, 3000) || '',
        primary_keyword: form.primary_keyword, seo_tags: form.seo_tags,
        linked_scope: editDoc?.linked_scope || '',
        board: editDoc?.board_name || ((editDoc?.geo_tags || '').toLowerCase().includes('seba') ? 'SEBA' : (editDoc?.geo_tags || '').toLowerCase().includes('degree') ? 'DEGREE' : 'AHSEC'),
        subject: form.category || form.seo_tags?.split(',')[0] || '',
      };
      const { data } = await axios.post(`${API}/admin/seo/generate`, payload, authHeaders(adminToken));
      setSeoResult(data);
      toast.success('SEO metadata generated — review and apply below');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'AI SEO generation failed');
    } finally { setSeoGenerating(false); }
  };

  const applySeoResult = () => {
    if (!seoResult) return;
    setForm(f => ({
      ...f,
      title: seoResult.seo_title || f.title,
      meta_description: seoResult.meta_description || f.meta_description,
      primary_keyword: seoResult.primary_keyword || f.primary_keyword,
      seo_tags: seoResult.seo_tags || f.seo_tags,
    }));
    setSeoResult(null);
    toast.success('SEO metadata applied to page');
  };

  const handleAutoGeoTags = () => {
    const parts = [];
    if (form.title) parts.push(form.title);
    if (form.geo_tags) parts.push(form.geo_tags);
    if (form.seo_tags) form.seo_tags.split(',').slice(0, 3).forEach(t => parts.push(t.trim()));
    if (!parts.length) { toast.error('Add title and GEO tags first'); return; }
    const phrases = [...new Set(parts.filter(Boolean))];
    setForm(f => ({ ...f, geo_tags: phrases.join(', ') }));
    toast.success(`${phrases.length} authority phrases set`);
  };

  const filtered = docs.filter(d => {
    const matchQ = !searchQ || d.title?.toLowerCase().includes(searchQ.toLowerCase()) || d.seo_slug?.includes(searchQ);
    const matchType = filterType === 'all' ? true
      : filterType === 'published' ? d.status === 'published'
      : filterType === 'draft' ? d.status === 'draft'
      : filterType === 'syllabus' ? d.type === 'syllabus'
      : filterType === 'revision' ? d.is_revision === true
      : true;
    return matchQ && matchType;
  });

  const inEditor = editDoc !== null || isNewDoc || form.title || form.content;
  const canPreview = showPreview && !!form.seo_slug;

  const selectStyle = {
    color: '#374151', background: '#f3f4f6',
    border: '1px solid #e5e7eb', borderRadius: 8,
    padding: '4px 8px', fontSize: 11, outline: 'none',
  };

  return {
    docs, loading, editDoc, form, setForm,
    saving, publishing, pdfLoading, aiParsing,
    seoGenerating, seoResult, setSeoResult,
    searchQ, setSearchQ, seoTab, setSeoTab,
    filterType, setFilterType,
    showPreview, setShowPreview,
    syllabusOpen, setSyllabusOpen,
    spBoards, spClasses, spStreams, spSubjects,
    spBoard, setSpBoard, spClass, setSpClass,
    spStream, setSpStream, spSubject, setSpSubject,
    syllabusInserting, savingRevision,
    linkingScope, linkedScopeLabel,
    scopePickerOpen, setScopePickerOpen,
    translateOpen, setTranslateOpen,
    translateLang, setTranslateLang,
    translating, translateResult, setTranslateResult,
    aiPaletteOpen, setAiPaletteOpen,
    aiPaletteText, setAiPaletteText,
    aiPaletteAction, setAiPaletteAction,
    aiPaletteResult, setAiPaletteResult,
    aiPaletteLoading,
    pdfRef, editorRef,
    filtered, inEditor, canPreview, selectStyle,
    openNew, openEdit,
    handleTitleChange, handleAiParse, handleSave,
    handlePublishToggle, handleDelete,
    handlePdfUpload, handleTranslate,
    handleAiPalette, applyAiPaletteResult,
    handleInsertSyllabus, handleSaveRevision,
    handleHandOff, handleLinkSyllabus,
    handleAutoKeyword, handleGenerateSeoMeta,
    applySeoResult, handleAutoGeoTags,
  };
}
