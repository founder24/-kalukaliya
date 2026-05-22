import {
  Save, Loader2, Globe, Lock, Link2,
  Sparkles, Monitor, FileUp, GitBranch, ArrowRightLeft, Languages,
} from 'lucide-react';

const STATUS_COLORS = {
  published: { bg: 'rgba(16,185,129,0.15)', border: 'rgba(16,185,129,0.35)', text: '#34d399' },
  draft:     { bg: 'rgba(100,116,139,0.15)', border: 'rgba(100,116,139,0.35)', text: '#94a3b8' },
};

export default function EditorToolbar({
  form, editDoc, linkedScopeLabel,
  handleTitleChange, handleSave, handlePublishToggle,
  handleSaveRevision, handleHandOff,
  saving, publishing, savingRevision, pdfLoading,
  showPreview, setShowPreview,
  pdfRef, handlePdfUpload,
  aiPaletteOpen, setAiPaletteOpen, setAiPaletteResult,
  translateOpen, setTranslateOpen, setTranslateResult,
}) {
  return (
    <div className="h-14 flex-shrink-0 border-b flex items-center px-4 gap-2" style={{ background: '#ffffff', borderColor: '#e5e7eb' }}>
      <div className="flex-1 min-w-0">
        <input
          value={form.title}
          onChange={e => handleTitleChange(e.target.value)}
          placeholder="Document title…"
          className="w-full text-lg font-bold bg-transparent outline-none truncate"
          style={{ color: '#374151' }}
        />
      </div>

      {editDoc && (
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border flex-shrink-0"
          style={{ background: STATUS_COLORS[form.status]?.bg, borderColor: STATUS_COLORS[form.status]?.border, color: STATUS_COLORS[form.status]?.text }}>
          {form.status === 'published' ? <Globe size={11} /> : <Lock size={11} />}
          {form.status}
        </div>
      )}

      {linkedScopeLabel && (
        <div className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-medium border flex-shrink-0"
          style={{ background: 'rgba(16,185,129,0.10)', borderColor: 'rgba(16,185,129,0.25)', color: '#34d399' }}>
          <Link2 size={9} /> Linked
        </div>
      )}

      <button onClick={() => setShowPreview(v => !v)} title="Toggle live preview"
        className="h-8 px-2.5 rounded-lg flex items-center gap-1.5 text-xs font-medium flex-shrink-0 border transition-all"
        style={showPreview
          ? { background: 'rgba(124,58,237,0.20)', color: '#7c3aed', borderColor: 'rgba(124,58,237,0.35)' }
          : { background: '#f3f4f6', color: '#6b7280', borderColor: '#e5e7eb' }}>
        <Monitor size={12} /> Preview
      </button>

      <input ref={pdfRef} type="file" accept=".pdf" className="hidden" onChange={handlePdfUpload} />
      <button onClick={() => pdfRef.current?.click()} disabled={pdfLoading}
        className="h-8 px-2.5 rounded-lg flex items-center gap-1.5 text-xs font-medium disabled:opacity-40 flex-shrink-0 border"
        style={{ background: 'rgba(59,130,246,0.15)', color: '#60a5fa', borderColor: 'rgba(59,130,246,0.20)' }}>
        {pdfLoading ? <Loader2 size={12} className="animate-spin" /> : <FileUp size={12} />}
        PDF
      </button>

      <button onClick={() => { setAiPaletteOpen(v => !v); setAiPaletteResult(''); }}
        className="h-8 px-2.5 rounded-lg flex items-center gap-1.5 text-xs font-medium flex-shrink-0 border"
        style={aiPaletteOpen
          ? { background: 'rgba(139,92,246,0.25)', color: '#c4b5fd', borderColor: 'rgba(139,92,246,0.45)' }
          : { background: 'rgba(139,92,246,0.10)', color: '#a78bfa', borderColor: 'rgba(139,92,246,0.22)' }}>
        <Sparkles size={12} /> AI Write
      </button>

      <button onClick={() => { setTranslateOpen(v => !v); setTranslateResult(''); }}
        className="h-8 px-2.5 rounded-lg flex items-center gap-1.5 text-xs font-medium flex-shrink-0 border"
        style={translateOpen
          ? { background: 'rgba(16,185,129,0.20)', color: '#34d399', borderColor: 'rgba(16,185,129,0.35)' }
          : { background: 'rgba(16,185,129,0.10)', color: '#34d399', borderColor: 'rgba(16,185,129,0.20)' }}>
        <Languages size={12} /> Translate
      </button>

      {editDoc && (
        <button onClick={handleSaveRevision} disabled={savingRevision} title="Save as new revision"
          className="h-8 px-2.5 rounded-lg flex items-center gap-1.5 text-xs font-medium disabled:opacity-40 flex-shrink-0 border"
          style={{ background: 'rgba(245,158,11,0.12)', color: '#fbbf24', borderColor: 'rgba(245,158,11,0.20)' }}>
          {savingRevision ? <Loader2 size={12} className="animate-spin" /> : <GitBranch size={12} />}
          Revision
        </button>
      )}

      {editDoc && (
        <button onClick={handleHandOff} title="Hand off content to Content Editor"
          className="h-8 px-2.5 rounded-lg flex items-center gap-1.5 text-xs font-medium flex-shrink-0 border"
          style={{ background: 'rgba(99,102,241,0.12)', color: '#818cf8', borderColor: 'rgba(99,102,241,0.22)' }}>
          <ArrowRightLeft size={12} /> Hand Off
        </button>
      )}

      <button onClick={handlePublishToggle} disabled={publishing || !editDoc}
        className="h-8 px-2.5 rounded-lg flex items-center gap-1.5 text-xs font-medium disabled:opacity-40 flex-shrink-0 border"
        style={form.status === 'published'
          ? { background: 'rgba(245,158,11,0.15)', color: '#fbbf24', borderColor: 'rgba(245,158,11,0.20)' }
          : { background: 'rgba(16,185,129,0.15)', color: '#34d399', borderColor: 'rgba(16,185,129,0.20)' }}>
        {publishing ? <Loader2 size={12} className="animate-spin" /> : <Globe size={12} />}
        {form.status === 'published' ? 'Unpublish' : 'Publish'}
      </button>

      <button onClick={handleSave} disabled={saving || !form.title.trim()}
        className="h-8 px-3 rounded-lg flex items-center gap-1.5 text-xs font-semibold disabled:opacity-40 flex-shrink-0"
        style={{ background: '#7c3aed', color: 'white' }}>
        {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
        {saving ? 'Saving…' : 'Save'}
      </button>
    </div>
  );
}
