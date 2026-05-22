import {
  MDXEditor,
  headingsPlugin, listsPlugin, quotePlugin, thematicBreakPlugin,
  markdownShortcutPlugin, codeBlockPlugin, codeMirrorPlugin, tablePlugin,
  linkPlugin, diffSourcePlugin, toolbarPlugin,
} from '@mdxeditor/editor';
import {
  Loader2, BookOpen, Eye, Copy, Sparkles, Zap,
  CheckCircle, ChevronDown, ChevronRight as ChevronRightIcon,
  Languages, X,
} from 'lucide-react';
import { toast } from 'sonner';
import MdxToolbar from './MdxToolbar';

const TEMPLATES = [
  { label: 'PYQ Block',      shortcode: '\n\n> **[PYQ year=2025]** _Question text here._ *(3 marks)*\n\n' },
  { label: 'Formula Box',    shortcode: '\n\n> **[FORMULA]** Name: `expression = result`\n\n' },
  { label: 'AHSEC Tip',      shortcode: '\n\n> **[BOARD-TIP]** This topic is important for board exams.\n\n' },
  { label: 'Note Block',     shortcode: '\n\n> **[NOTE]** Key insight or definition here.\n\n' },
  { label: 'H2 Section',     shortcode: '\n\n## Section Title\n\n_Content here._\n\n---\n\n' },
  { label: 'Syllabus Intro', shortcode: '\n\n## Syllabus Overview\n\nThis document covers the official syllabus as per the board guidelines.\n\n### Key Topics\n\n- Topic 1\n- Topic 2\n- Topic 3\n\n### Chapters\n\n1. Chapter 1\n2. Chapter 2\n\n### Exam Guidelines\n\n_As per official board regulations._\n\n---\n\n' },
  { label: 'Chapter Link',   shortcode: '\n\n[Chapter: Title](/learn/chapter-slug)\n\n' },
];

export default function ContentTab({
  form, setForm, editDoc, editorRef,
  handleAiParse, aiParsing, canPreview,
  syllabusOpen, setSyllabusOpen,
  spBoard, setSpBoard, spBoards,
  spClass, setSpClass, spClasses,
  spStream, setSpStream, spStreams,
  spSubject, setSpSubject, spSubjects,
  syllabusInserting, handleInsertSyllabus,
  translateOpen, setTranslateOpen,
  translateLang, setTranslateLang,
  translating, handleTranslate, translateResult, setTranslateResult,
  aiPaletteOpen, setAiPaletteOpen,
  aiPaletteText, setAiPaletteText,
  aiPaletteAction, setAiPaletteAction,
  aiPaletteResult, setAiPaletteResult,
  aiPaletteLoading, handleAiPalette, applyAiPaletteResult,
  selectStyle,
}) {
  return (
    <div className="flex-1 flex flex-col overflow-hidden min-h-0">
      <div className="flex-shrink-0 border-b" style={{ borderColor: '#e5e7eb', background: '#ffffff' }}>
        <div className="flex items-center gap-1.5 px-4 py-2 flex-wrap">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" strokeWidth="2" className="flex-shrink-0"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
          <span className="text-[10px] flex-shrink-0 mr-0.5" style={{ color: '#9ca3af' }}>Insert:</span>
          {TEMPLATES.map(t => (
            <button key={t.label}
              onClick={() => {
                const current = editorRef.current?.getMarkdown() || form.content;
                setForm(f => ({ ...f, content: current + t.shortcode }));
              }}
              className="px-2 py-0.5 rounded text-[10px] border transition-colors"
              style={{ borderColor: '#e5e7eb', background: '#f9fafb', color: '#6b7280' }}
              onMouseEnter={e => { e.currentTarget.style.color = '#7c3aed'; e.currentTarget.style.borderColor = 'rgba(124,58,237,0.15)'; }}
              onMouseLeave={e => { e.currentTarget.style.color = '#6b7280'; e.currentTarget.style.borderColor = '#e5e7eb'; }}
            >
              {t.label}
            </button>
          ))}
          <button
            onClick={() => setSyllabusOpen(v => !v)}
            className="ml-1 px-2 py-0.5 rounded text-[10px] border flex items-center gap-1 transition-colors"
            style={syllabusOpen
              ? { borderColor: 'rgba(124,58,237,0.20)', background: 'rgba(124,58,237,0.08)', color: '#7c3aed' }
              : { borderColor: 'rgba(124,58,237,0.12)', background: 'rgba(149,117,224,0.07)', color: 'rgba(196,176,240,0.65)' }}>
            <BookOpen size={9} />
            Insert Syllabus
            {syllabusOpen ? <ChevronDown size={9} /> : <ChevronRightIcon size={9} />}
          </button>
        </div>

        {syllabusOpen && (
          <div className="px-4 pb-3 flex items-end gap-2 flex-wrap" style={{ borderTop: '1px solid #f3f4f6' }}>
            <div>
              <p className="text-[10px] mb-1" style={{ color: '#9ca3af' }}>Board</p>
              <select value={spBoard} onChange={e => setSpBoard(e.target.value)} style={selectStyle}>
                <option value="">— Board —</option>
                {spBoards.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </div>
            <div>
              <p className="text-[10px] mb-1" style={{ color: '#9ca3af' }}>Class</p>
              <select value={spClass} onChange={e => setSpClass(e.target.value)} disabled={!spBoard} style={selectStyle}>
                <option value="">— Class —</option>
                {spClasses.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div>
              <p className="text-[10px] mb-1" style={{ color: '#9ca3af' }}>Stream</p>
              <select value={spStream} onChange={e => setSpStream(e.target.value)} disabled={!spClass} style={selectStyle}>
                <option value="">— Stream —</option>
                {spStreams.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <div>
              <p className="text-[10px] mb-1" style={{ color: '#9ca3af' }}>Subject</p>
              <select value={spSubject} onChange={e => setSpSubject(e.target.value)} disabled={!spStream} style={selectStyle}>
                <option value="">— Subject —</option>
                {spSubjects.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
            <button onClick={handleInsertSyllabus} disabled={syllabusInserting || !spBoard || !spClass}
              className="h-8 px-3 rounded-lg flex items-center gap-1.5 text-xs font-medium disabled:opacity-40"
              style={{ background: '#7c3aed', color: 'white' }}>
              {syllabusInserting ? <Loader2 size={11} className="animate-spin" /> : <BookOpen size={11} />}
              Insert
            </button>
          </div>
        )}
      </div>

      {translateOpen && (
        <div style={{ background: 'rgba(16,185,129,0.06)', borderBottom: '1px solid rgba(16,185,129,0.18)', padding: '10px 16px' }}>
          <div className="flex items-center gap-3 flex-wrap">
            <Languages size={14} color="#34d399" />
            <span style={{ fontSize: 12, fontWeight: 700, color: '#34d399' }}>Gemini Translate</span>
            <select value={translateLang} onChange={e => setTranslateLang(e.target.value)}
              style={{ background: '#e5e7eb', border: '1px solid rgba(16,185,129,0.30)', borderRadius: 8, padding: '4px 10px', color: '#111827', fontSize: 12 }}>
              <option value="as">Assamese (অসমীয়া)</option>
              <option value="hi">Hindi (हिन्दी)</option>
              <option value="bn">Bengali (বাংলা)</option>
              <option value="bho">Bodo (बड़ो)</option>
            </select>
            <button onClick={handleTranslate} disabled={translating}
              style={{ background: 'rgba(16,185,129,0.2)', border: '1px solid rgba(16,185,129,0.35)', color: '#34d399', borderRadius: 8, padding: '4px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}>
              {translating ? <Loader2 size={12} className="animate-spin" /> : <Languages size={12} />}
              {translating ? 'Translating…' : 'Translate Content'}
            </button>
            <button onClick={() => setTranslateOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', marginLeft: 'auto' }}>
              <X size={14} />
            </button>
          </div>
          {translateResult && (
            <div style={{ marginTop: 10, background: '#f9fafb', border: '1px solid rgba(16,185,129,0.20)', borderRadius: 8, padding: '10px 14px', maxHeight: 180, overflowY: 'auto' }}>
              <div className="flex items-center justify-between mb-2">
                <span style={{ fontSize: 10, fontWeight: 700, color: '#34d399', textTransform: 'uppercase' }}>Translation Result</span>
                <button onClick={() => { navigator.clipboard.writeText(translateResult); toast.success('Copied!'); }}
                  style={{ background: 'rgba(16,185,129,0.15)', border: '1px solid rgba(16,185,129,0.3)', color: '#34d399', borderRadius: 6, padding: '2px 8px', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                  <Copy size={10} /> Copy
                </button>
              </div>
              <p style={{ fontSize: 13, color: '#111827', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{translateResult}</p>
            </div>
          )}
        </div>
      )}

      {aiPaletteOpen && (
        <div style={{ background: 'rgba(139,92,246,0.07)', borderBottom: '1px solid rgba(139,92,246,0.22)', padding: '10px 16px' }}>
          <div className="flex items-center gap-3 flex-wrap mb-2">
            <Sparkles size={14} color="#a78bfa" />
            <span style={{ fontSize: 12, fontWeight: 700, color: '#a78bfa' }}>Gemini AI Palette</span>
            <select value={aiPaletteAction} onChange={e => setAiPaletteAction(e.target.value)}
              style={{ background: '#e5e7eb', border: '1px solid rgba(139,92,246,0.30)', borderRadius: 8, padding: '4px 10px', color: '#111827', fontSize: 12 }}>
              <option value="improve">Improve writing</option>
              <option value="simplify">Simplify</option>
              <option value="expand">Expand explanation</option>
              <option value="summarize">Summarize</option>
              <option value="rewrite">Rewrite formally</option>
              <option value="bullets">Convert to bullets</option>
            </select>
            <button onClick={handleAiPalette} disabled={aiPaletteLoading || !aiPaletteText.trim()}
              style={{ background: 'rgba(139,92,246,0.20)', border: '1px solid rgba(139,92,246,0.40)', color: '#c4b5fd', borderRadius: 8, padding: '4px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, opacity: aiPaletteLoading || !aiPaletteText.trim() ? 0.5 : 1 }}>
              {aiPaletteLoading ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
              {aiPaletteLoading ? 'Rewriting…' : 'Run'}
            </button>
            <button onClick={() => setAiPaletteOpen(false)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', marginLeft: 'auto' }}>
              <X size={14} />
            </button>
          </div>
          <textarea
            value={aiPaletteText}
            onChange={e => { setAiPaletteText(e.target.value); setAiPaletteResult(''); }}
            placeholder="Paste or type the text you want Gemini to rewrite…"
            rows={3}
            style={{ width: '100%', background: '#e5e7eb', border: '1px solid rgba(139,92,246,0.20)', borderRadius: 8, padding: '8px 12px', color: '#111827', fontSize: 12, resize: 'vertical', outline: 'none' }}
          />
          {aiPaletteResult && (
            <div style={{ marginTop: 8, background: '#f9fafb', border: '1px solid rgba(139,92,246,0.20)', borderRadius: 8, padding: '10px 14px' }}>
              <div className="flex items-center justify-between mb-2">
                <span style={{ fontSize: 10, fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase' }}>Suggestion</span>
                <div className="flex items-center gap-2">
                  <button onClick={() => { navigator.clipboard.writeText(aiPaletteResult); toast.success('Copied!'); }}
                    style={{ background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)', color: '#a78bfa', borderRadius: 6, padding: '2px 8px', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Copy size={10} /> Copy
                  </button>
                  <button onClick={applyAiPaletteResult}
                    style={{ background: 'rgba(139,92,246,0.25)', border: '1px solid rgba(139,92,246,0.4)', color: '#c4b5fd', borderRadius: 6, padding: '2px 8px', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <CheckCircle size={10} /> Apply to Content
                  </button>
                </div>
              </div>
              <p style={{ fontSize: 13, color: '#111827', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{aiPaletteResult}</p>
            </div>
          )}
        </div>
      )}

      <div className={`flex-1 min-h-0 flex ${canPreview ? 'gap-0' : ''} overflow-hidden`}>
        <div className={`${canPreview ? 'w-1/2 border-r' : 'flex-1'} overflow-hidden cms-light-editor-wrapper`}
          data-color-mode="light"
          style={{ backgroundColor: '#ffffff', color: '#1a1a1a', borderColor: 'rgba(0,0,0,0.08)' }}>
          <MDXEditor
            ref={editorRef}
            key={editDoc?.id ?? '__new__'}
            markdown={form.content || ''}
            onChange={md => setForm(f => ({ ...f, content: md }))}
            plugins={[
              headingsPlugin(), listsPlugin(), quotePlugin(), thematicBreakPlugin(),
              markdownShortcutPlugin(),
              codeBlockPlugin({ defaultCodeBlockLanguage: 'text' }),
              codeMirrorPlugin({ codeBlockLanguages: { js: 'JavaScript', ts: 'TypeScript', python: 'Python', text: 'Text', md: 'Markdown', html: 'HTML', css: 'CSS' } }),
              tablePlugin(), linkPlugin(),
              diffSourcePlugin({ viewMode: 'rich-text', diffMarkdown: form.content || '' }),
              toolbarPlugin({ toolbarContents: () => <MdxToolbar onAiParse={handleAiParse} aiParsing={aiParsing} /> }),
            ]}
            className="mdx-editor-light h-full"
            contentEditableClassName="cms-editor-content"
          />
        </div>

        {canPreview && (
          <div className="w-1/2 flex flex-col overflow-hidden" style={{ background: '#ffffff' }}>
            <div className="flex items-center gap-2 px-3 py-1.5 border-b flex-shrink-0" style={{ borderColor: 'rgba(0,0,0,0.08)', background: '#f8f8f8' }}>
              <Eye size={11} style={{ color: '#6b7280' }} />
              <span className="text-[10px] font-mono" style={{ color: '#9ca3af' }}>/learn/{form.seo_slug}</span>
            </div>
            <iframe
              key={form.seo_slug}
              src={`/learn/${form.seo_slug}`}
              className="flex-1 w-full border-0"
              title="Live Preview"
            />
          </div>
        )}
      </div>
    </div>
  );
}
