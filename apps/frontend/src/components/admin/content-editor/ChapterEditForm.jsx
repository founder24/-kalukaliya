import { useRef, useState, useCallback } from 'react';
import {
  ArrowLeft, Save, Loader2, Eye, Link2, BarChart3,
  Sparkles, RefreshCw, Layers, LayoutTemplate, Upload,
  FileText, Globe, CheckCircle, Smartphone, Monitor,
  ImagePlus, Languages,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import axios from 'axios';
import { toast } from 'sonner';
import { TEMPLATES } from '@/utils/editorTemplates';
import { API, autoSlug, authHeaders } from '@/utils/adminHelpers';
import PYQUploadPanel from './PYQUploadPanel';
import {
  MDXEditor,
  headingsPlugin, listsPlugin, quotePlugin, thematicBreakPlugin,
  markdownShortcutPlugin, codeBlockPlugin, codeMirrorPlugin, tablePlugin,
  linkPlugin, diffSourcePlugin, toolbarPlugin, imagePlugin,
  UndoRedo, BoldItalicUnderlineToggles, BlockTypeSelect,
  CreateLink, CodeToggle, InsertTable, InsertThematicBreak,
  ListsToggle, Separator, DiffSourceToggleWrapper, InsertCodeBlock,
  InsertImage,
} from '@mdxeditor/editor';
import '@mdxeditor/editor/style.css';

const CONTENT_TYPES = [
  { value: 'notes', label: 'Notes', color: 'violet' },
  { value: 'question_paper', label: 'Question Paper', color: 'amber' },
  { value: 'formula', label: 'Formula Sheet', color: 'pink' },
  { value: 'summary', label: 'Summary', color: 'emerald' },
  { value: 'solution', label: 'Solution', color: 'blue' },
  { value: 'reference', label: 'Reference', color: 'slate' },
];

export default function ChapterEditForm({
  editView, editTarget, contentForm, setContentForm,
  subjectData, saving, chapterStats,
  onSave, onCancel, onFileAttach, uploading,
  onAiParse, aiParsing, onLoadChapterStats,
  editorRef, editorKey, setEditorKey,
  showPreview, setShowPreview,
  fileInputRef,
  adminToken, boardId, classId, streamId,
}) {
  const [mobilePreview, setMobilePreview] = useState(true);
  const [imgUploading, setImgUploading] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [editorLang, setEditorLang] = useState('en');
  const [translating, setTranslating] = useState(false);

  const handleTranslateToAssamese = useCallback(async () => {
    if (!editTarget?.id) return;
    setTranslating(true);
    const tid = toast.loading('Translating to Assamese…');
    try {
      const res = await axios.post(
        `${API}/admin/content/chapters/${editTarget.id}/translate`,
        { target_lang: 'as-IN' },
        authHeaders(adminToken)
      );
      setContentForm(f => ({ ...f, content_as: res.data.translated_text || '' }));
      setEditorKey(k => k + 1);
      toast.success(`Translated — ${res.data.word_count} words`, { id: tid });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Translation failed', { id: tid });
    } finally {
      setTranslating(false);
    }
  }, [editTarget?.id, adminToken, setContentForm]);

  const activeContent = editorLang === 'as' ? (contentForm.content_as || '') : contentForm.content;
  const handleContentChange = useCallback((md) => {
    if (editorLang === 'as') {
      setContentForm(f => ({ ...f, content_as: md }));
    } else {
      setContentForm(f => ({ ...f, content: md }));
    }
  }, [editorLang, setContentForm]);

  const imageUploadHandler = useCallback(async (image) => {
    const formData = new FormData();
    formData.append('file', image);
    const res = await axios.post(`${API}/admin/content/upload-image`, formData, {
      ...authHeaders(adminToken),
      headers: { ...authHeaders(adminToken).headers, 'Content-Type': 'multipart/form-data' },
    });
    return res.data.url;
  }, [adminToken]);

  const handleAddPages = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.multiple = true;
    input.onchange = async (e) => {
      const files = Array.from(e.target.files || []);
      if (!files.length) return;
      if (files.some(f => f.size > 10 * 1024 * 1024)) {
        toast.error('Each image must be under 10 MB');
        return;
      }
      setImgUploading(true);
      const tid = toast.loading(`Uploading ${files.length} page(s)…`);
      try {
        const urls = [];
        for (let i = 0; i < files.length; i++) {
          toast.loading(`Page ${i + 1}/${files.length}…`, { id: tid });
          urls.push(await imageUploadHandler(files[i]));
        }
        const md = editorRef.current?.getMarkdown?.() ?? activeContent;
        const pagesMd = urls.map((u, i) => `![Page ${i + 1}](${u})`).join('\n\n');
        const field = editorLang === 'as' ? 'content_as' : 'content';
        setContentForm(f => ({ ...f, [field]: md + (md.trim() ? '\n\n' : '') + pagesMd + '\n' }));
        setEditorKey(k => k + 1);
        toast.success(`${urls.length} page(s) added`, { id: tid });
      } catch {
        toast.error('Upload failed', { id: tid });
      } finally {
        setImgUploading(false);
      }
    };
    input.click();
  }, [imageUploadHandler, editorRef, activeContent, editorLang, setContentForm, setEditorKey]);

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-6 pt-5 pb-3 flex-shrink-0">
        <button onClick={onCancel} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-gray-900 mb-4 transition-colors"><ArrowLeft size={15} /> Back</button>
        <h3 className="text-xl font-bold text-gray-900 mb-0.5">{editView === 'edit-chapter' ? 'Edit Chapter' : 'Create Chapter'}</h3>
        <p className="text-gray-500 text-xs">{subjectData?.name}</p>
      </div>

      <div className="flex-1 flex flex-col min-h-0 px-6 pb-6 gap-3 overflow-y-auto">
        <div className="flex-shrink-0 grid grid-cols-1 lg:grid-cols-2 gap-2.5">
          <div>
            <label className="text-xs text-gray-500 block mb-1">Title *</label>
            <input value={contentForm.title} onChange={(e) => { const title = e.target.value; setContentForm(f => ({ ...f, title, slug: f.slug === autoSlug(f.title) || !f.slug ? autoSlug(title) : f.slug })); }} placeholder="Chapter title" className="w-full h-10 px-3.5 rounded-lg text-sm text-gray-900 bg-gray-50 border border-gray-200 outline-none focus:border-violet-500 transition-colors" />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">URL Slug</label>
            <div className="flex items-center h-10 rounded-lg bg-gray-50 border border-gray-200 overflow-hidden">
              <span className="px-2.5 text-gray-400 flex-shrink-0"><Link2 size={11} /></span>
              <input value={contentForm.slug} onChange={(e) => setContentForm({ ...contentForm, slug: e.target.value })} placeholder="auto-slug" className="flex-1 h-full text-xs text-gray-900 bg-transparent outline-none font-mono pr-3" />
            </div>
          </div>
        </div>

        <div className="flex-shrink-0 grid grid-cols-1 lg:grid-cols-2 gap-2.5">
          <div>
            <label className="text-xs text-gray-500 block mb-1">Type</label>
            <div className="flex flex-wrap gap-1">
              {CONTENT_TYPES.map(ct => (
                <button
                  key={ct.value}
                  onClick={() => setContentForm(f => ({ ...f, content_type: ct.value }))}
                  className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-all border ${contentForm.content_type === ct.value ? 'border-violet-500 bg-violet-500/20 text-violet-600' : 'border-gray-200 bg-gray-50 text-gray-500 hover:text-gray-700 hover:border-gray-300'}`}
                >
                  {ct.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Description</label>
            <input value={contentForm.description} onChange={(e) => setContentForm({ ...contentForm, description: e.target.value })} placeholder="Brief description…" className="w-full h-10 px-3.5 rounded-lg text-sm text-gray-900 bg-gray-50 border border-gray-200 outline-none focus:border-violet-500 transition-colors" />
          </div>
        </div>

        <div className="flex-shrink-0">
          <label className="text-xs text-gray-500 block mb-1">Topics <span className="text-gray-400">(comma-separated)</span></label>
          <input
            value={(contentForm.topics || []).join(', ')}
            onChange={(e) => setContentForm(f => ({ ...f, topics: e.target.value.split(',').map(t => t.trim()).filter(Boolean) }))}
            placeholder="e.g. Photosynthesis, Carbon cycle"
            className="w-full h-10 px-3.5 rounded-lg text-sm text-gray-900 bg-gray-50 border border-gray-200 outline-none focus:border-violet-500 transition-colors"
          />
          {(contentForm.topics || []).length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {contentForm.topics.map((topic, i) => (
                <span key={i} className="px-2 py-0.5 rounded-full text-[10px] bg-violet-500/10 text-violet-600 border border-violet-500/20 flex items-center gap-1">
                  {topic}
                  <button onClick={() => setContentForm(f => ({ ...f, topics: f.topics.filter((_, j) => j !== i) }))} className="text-gray-400 hover:text-red-400 transition-colors">×</button>
                </span>
              ))}
            </div>
          )}
        </div>

        {chapterStats && (
          <div className="flex-shrink-0 flex items-center gap-2.5 px-3 py-2 rounded-lg bg-gray-50 border border-gray-200 text-[11px] flex-wrap">
            <div className="flex items-center gap-1 text-gray-500">
              <BarChart3 size={11} className="text-violet-400" />
              <span>{chapterStats.chunk_count} chunks</span>
            </div>
            <span className="text-gray-400">{chapterStats.content_length?.toLocaleString()} chars</span>
            {chapterStats.notes_generated && <span className="flex items-center gap-0.5 text-emerald-500"><CheckCircle size={10} />Notes</span>}
            {(chapterStats.pyq_count || 0) > 0 && <span className="flex items-center gap-0.5 text-amber-500"><FileText size={10} />{chapterStats.pyq_count} PYQs</span>}
            {(chapterStats.flashcard_count || 0) > 0 && <span className="flex items-center gap-0.5 text-emerald-500"><Layers size={10} />{chapterStats.flashcard_count} cards</span>}
            <button onClick={() => onLoadChapterStats(editTarget?.id)} className="ml-auto text-gray-400 hover:text-gray-700 p-0.5 transition-colors"><RefreshCw size={10} /></button>
          </div>
        )}

        {(editView === 'edit-chapter' || editView === 'new-chapter') && contentForm.content_type !== 'question_paper' && (
          <div className="flex-shrink-0 flex items-center gap-2 px-3 py-2 rounded-lg bg-violet-50/50 border border-violet-200/50">
            <Languages size={14} className="text-violet-500 shrink-0" />
            <div className="flex items-center gap-0.5 rounded-md p-0.5" style={{ background: 'rgba(139,92,246,0.1)' }}>
              <button
                onClick={() => { setEditorLang('en'); setEditorKey(k => k + 1); }}
                className={`px-2.5 py-1 rounded text-[11px] font-semibold transition-all ${editorLang === 'en' ? 'text-white bg-violet-600 shadow-sm' : 'text-violet-600 hover:bg-violet-100'}`}
              >
                English
              </button>
              <button
                onClick={() => { setEditorLang('as'); setEditorKey(k => k + 1); }}
                className={`px-2.5 py-1 rounded text-[11px] font-semibold transition-all ${editorLang === 'as' ? 'text-white bg-violet-600 shadow-sm' : 'text-violet-600 hover:bg-violet-100'}`}
              >
                অসমীয়া
              </button>
            </div>
            {editorLang === 'as' && (
              contentForm.content_as ? (
                <span className="text-[10px] text-emerald-600 bg-emerald-50 px-1.5 py-0.5 rounded-full border border-emerald-200">{contentForm.content_as.split(/\s+/).length} words</span>
              ) : (
                <span className="text-[10px] text-gray-400">No Assamese content</span>
              )
            )}
            {!contentForm.content_as && editTarget?.id && (
              <button
                onClick={handleTranslateToAssamese}
                disabled={translating || !contentForm.content?.trim()}
                className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold bg-violet-500 text-white hover:bg-violet-600 disabled:opacity-50 transition-all shadow-sm"
              >
                {translating ? <Loader2 size={12} className="animate-spin" /> : <Languages size={12} />}
                {translating ? 'Translating…' : 'Translate to অসমীয়া'}
              </button>
            )}
            {!contentForm.content_as && !editTarget?.id && editorLang === 'as' && (
              <span className="ml-auto text-[10px] text-amber-600 bg-amber-50 px-2 py-1 rounded-md border border-amber-200">
                Save chapter first to use auto-translate
              </span>
            )}
            {contentForm.content_as && (
              <button
                onClick={handleTranslateToAssamese}
                disabled={translating || !contentForm.content?.trim() || !editTarget?.id}
                className="ml-auto flex items-center gap-1.5 px-2 py-1 rounded-lg text-[10px] font-medium text-violet-500 hover:bg-violet-100 disabled:opacity-50 transition-all border border-violet-200"
              >
                {translating ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
                {translating ? 'Translating…' : 'Re-translate'}
              </button>
            )}
          </div>
        )}

        {editView === 'edit-chapter' && editTarget?.id && (
          <div className="flex-shrink-0">
            <PYQUploadPanel
              adminToken={adminToken}
              chapterId={editTarget.id}
              subjectId={subjectData?.id || ''}
              boardId={boardId || ''}
              classId={classId || ''}
              streamId={streamId || ''}
            />
          </div>
        )}

        <div className="flex-1 flex flex-col min-h-0">
          <div className="flex items-center gap-1.5 mb-1.5 flex-shrink-0">
            <div className="flex items-center gap-1">
              <button
                onClick={handleAddPages}
                disabled={imgUploading}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50 transition-all shadow-sm"
              >
                {imgUploading ? <Loader2 size={12} className="animate-spin" /> : <ImagePlus size={12} />}
                {imgUploading ? 'Uploading…' : 'Add Pages'}
              </button>
              <button
                onClick={() => setShowTemplates(v => !v)}
                className={`flex items-center gap-1 px-2 py-1.5 rounded-lg text-[11px] font-medium border transition-all ${showTemplates ? 'bg-violet-50 text-violet-600 border-violet-200' : 'bg-gray-50 text-gray-400 border-gray-200 hover:text-gray-600'}`}
              >
                <LayoutTemplate size={10} />
                Templates
              </button>
            </div>
            <div className="ml-auto flex items-center gap-1.5">
              <span className="text-[10px] text-gray-300 font-mono">{activeContent.length}</span>
              <button
                onClick={() => setMobilePreview(p => !p)}
                className={`p-1.5 rounded-lg text-[10px] border transition-all ${mobilePreview ? 'bg-violet-50 text-violet-500 border-violet-200' : 'bg-gray-50 text-gray-400 border-gray-200 hover:text-gray-600'}`}
                title={mobilePreview ? 'Desktop width' : 'Mobile width'}
              >
                {mobilePreview ? <Smartphone size={12} /> : <Monitor size={12} />}
              </button>
              <button
                onClick={() => setShowPreview(p => !p)}
                className={`p-1.5 rounded-lg text-[10px] border transition-all ${showPreview ? 'bg-violet-50 text-violet-500 border-violet-200' : 'bg-gray-50 text-gray-400 border-gray-200 hover:text-gray-600'}`}
                title={showPreview ? 'Hide preview' : 'Show preview'}
              >
                <Eye size={12} />
              </button>
            </div>
          </div>

          {showTemplates && (
            <div className="flex flex-wrap gap-1 mb-1.5 flex-shrink-0 animate-in fade-in slide-in-from-top-1 duration-150">
              {TEMPLATES.map(t => (
                <button
                  key={t.label}
                  onClick={() => {
                    const current = editorRef.current?.getMarkdown?.() ?? activeContent;
                    const field = editorLang === 'as' ? 'content_as' : 'content';
                    setContentForm(f => ({ ...f, [field]: current + t.shortcode }));
                    setEditorKey(k => k + 1);
                  }}
                  className="px-2 py-0.5 rounded text-[10px] border border-gray-200 bg-white text-gray-500 hover:text-violet-500 hover:border-violet-300 transition-colors"
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}

          <div className={`flex-1 min-h-0 flex ${showPreview ? 'gap-3' : 'flex-col'}`}>
            <div
              className={`flex-1 min-h-0 overflow-hidden flex flex-col transition-all duration-200 ${
                mobilePreview ? 'mx-auto' : ''
              }`}
              style={mobilePreview ? {
                width: 400,
                maxWidth: '100%',
                borderRadius: 24,
                border: '2px solid #d1d5db',
                boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
              } : {
                borderRadius: 12,
                border: '1px solid rgba(0,0,0,0.08)',
              }}
            >
              {mobilePreview && (
                <div className="flex items-center justify-center py-1 bg-gray-50 border-b border-gray-200" style={{ borderRadius: '22px 22px 0 0' }}>
                  <div className="w-14 h-[3px] rounded-full bg-gray-300" />
                </div>
              )}
              <div className="flex-1 min-h-0 overflow-hidden" style={{ background: '#fff' }}>
                <MDXEditor
                  ref={editorRef}
                  key={`${editTarget?.id ?? '__new__'}-${editorKey}-${editorLang}`}
                  markdown={activeContent}
                  onChange={handleContentChange}
                  className="mdx-editor-light h-full"
                  contentEditableClassName="cms-editor-content"
                  plugins={[
                    headingsPlugin(),
                    listsPlugin(),
                    quotePlugin(),
                    thematicBreakPlugin(),
                    markdownShortcutPlugin(),
                    codeBlockPlugin({ defaultCodeBlockLanguage: 'text' }),
                    codeMirrorPlugin({
                      codeBlockLanguages: { js: 'JavaScript', ts: 'TypeScript', python: 'Python', text: 'Text', html: 'HTML', css: 'CSS' },
                    }),
                    tablePlugin(),
                    linkPlugin(),
                    imagePlugin({ imageUploadHandler }),
                    diffSourcePlugin({ viewMode: 'rich-text', diffMarkdown: '' }),
                    toolbarPlugin({
                      toolbarContents: () => (
                        <DiffSourceToggleWrapper>
                          <UndoRedo />
                          <Separator />
                          <BoldItalicUnderlineToggles />
                          <Separator />
                          <ListsToggle />
                          <Separator />
                          <BlockTypeSelect />
                          <Separator />
                          <CreateLink />
                          <InsertImage />
                          <InsertTable />
                          <Separator />
                          <button
                            type="button"
                            onClick={onAiParse}
                            disabled={aiParsing}
                            style={{
                              display: 'flex', alignItems: 'center', gap: 4,
                              padding: '2px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                              color: '#a78bfa', background: 'rgba(167,139,250,0.10)',
                              border: '1px solid rgba(167,139,250,0.20)',
                              cursor: aiParsing ? 'not-allowed' : 'pointer',
                              opacity: aiParsing ? 0.5 : 1,
                            }}
                          >
                            {aiParsing
                              ? <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} />
                              : <Sparkles size={12} />}
                            AI
                          </button>
                        </DiffSourceToggleWrapper>
                      ),
                    }),
                  ]}
                />
              </div>
              {mobilePreview && (
                <div className="flex items-center justify-center py-0.5 bg-gray-50 border-t border-gray-200" style={{ borderRadius: '0 0 22px 22px' }}>
                  <div className="w-20 h-[3px] rounded-full bg-gray-300" />
                </div>
              )}
            </div>

            {showPreview && (
              <div
                className={`min-h-0 overflow-y-auto flex-1 transition-all duration-200 ${mobilePreview ? 'mx-auto' : ''}`}
                style={mobilePreview ? {
                  width: 400,
                  maxWidth: '100%',
                  borderRadius: 24,
                  border: '2px solid #d1d5db',
                  boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
                  background: '#fff',
                } : {
                  borderRadius: 12,
                  border: '1px solid rgba(0,0,0,0.08)',
                  background: '#fff',
                }}
              >
                {mobilePreview && (
                  <div className="flex items-center justify-center py-1 bg-gray-50 border-b border-gray-200 sticky top-0 z-10" style={{ borderRadius: '22px 22px 0 0' }}>
                    <div className="w-14 h-[3px] rounded-full bg-gray-300" />
                  </div>
                )}
                <div className="cms-preview-content" style={{ padding: mobilePreview ? '1rem 1.25rem' : '1.5rem 2rem', fontSize: '15px', lineHeight: '1.75', color: '#1a1a1a', minHeight: 200 }}>
                  {activeContent.trim() ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {activeContent}
                    </ReactMarkdown>
                  ) : (
                    <p style={{ color: '#bbb', fontStyle: 'italic', fontSize: 13 }}>Preview appears here…</p>
                  )}
                </div>
              </div>
            )}
          </div>

          {editView === 'edit-chapter' && editTarget?.id && (
            <div className="flex items-center gap-2 mt-1.5 flex-shrink-0">
              <input ref={fileInputRef} type="file" accept=".pdf,.txt,.md" className="hidden" onChange={() => onFileAttach(editTarget.id)} />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-blue-500 hover:bg-blue-50 transition-colors text-[11px] font-medium disabled:opacity-40"
              >
                {uploading ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />}
                Attach File
              </button>
            </div>
          )}
        </div>

        <div className="flex gap-2.5 flex-shrink-0 pt-1">
          <button onClick={onCancel} className="flex-1 h-11 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 font-medium text-sm transition-colors">Cancel</button>
          <button
            onClick={onSave}
            disabled={saving || !contentForm.title}
            className="flex-1 h-11 rounded-xl bg-violet-600 hover:bg-violet-500 text-white font-semibold text-sm disabled:opacity-40 flex items-center justify-center gap-2 transition-colors shadow-sm"
          >
            {saving ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
            {saving ? 'Saving…' : editView === 'edit-chapter' ? 'Update' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}
