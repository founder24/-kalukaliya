import {
  Loader2, Link2, Zap, CheckCircle,
  ChevronDown, ChevronRight as ChevronRightIcon,
} from 'lucide-react';

export default function GeoTagsTab({
  form, setForm, editDoc,
  handleAutoGeoTags, handleLinkSyllabus,
  linkedScopeLabel, linkingScope,
  scopePickerOpen, setScopePickerOpen,
  spBoard, setSpBoard, spBoards,
  spClass, setSpClass, spClasses,
  spStream, setSpStream, spStreams,
  spSubject, setSpSubject, spSubjects,
  selectStyle,
}) {
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-2xl mx-auto space-y-5">

        <div className="p-4 rounded-xl" style={{ background: 'rgba(124,58,237,0.04)', border: '1px solid rgba(149,117,224,0.14)' }}>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Link2 size={13} style={{ color: '#7c3aed' }} />
              <p className="text-xs font-semibold" style={{ color: '#7c3aed' }}>Link to Syllabus Scope</p>
            </div>
            {linkedScopeLabel && (
              <span className="text-[10px] px-2 py-0.5 rounded-full" style={{ background: 'rgba(16,185,129,0.15)', color: '#34d399' }}>
                <CheckCircle size={9} className="inline mr-1" />{linkedScopeLabel}
              </span>
            )}
          </div>
          <p className="text-[10px] mb-3" style={{ color: '#9ca3af' }}>
            Linking populates canonical URL and GEO tags automatically from the scope hierarchy.
          </p>
          <button onClick={() => setScopePickerOpen(v => !v)}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border"
            style={{ borderColor: 'rgba(124,58,237,0.15)', color: '#7c3aed', background: 'rgba(124,58,237,0.06)' }}>
            {scopePickerOpen ? <ChevronDown size={11} /> : <ChevronRightIcon size={11} />}
            {scopePickerOpen ? 'Close Picker' : 'Choose Scope'}
          </button>
          {scopePickerOpen && (
            <div className="mt-3 flex items-end gap-2 flex-wrap pt-3" style={{ borderTop: '1px solid rgba(124,58,237,0.08)' }}>
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
              <button onClick={handleLinkSyllabus} disabled={linkingScope || !editDoc || !spBoard || !spClass}
                className="h-8 px-3 rounded-lg flex items-center gap-1.5 text-xs font-medium disabled:opacity-40"
                style={{ background: '#7c3aed', color: 'white' }}>
                {linkingScope ? <Loader2 size={11} className="animate-spin" /> : <Link2 size={11} />}
                Link Scope
              </button>
            </div>
          )}
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs" style={{ color: '#6b7280' }}>GEO Tags <span style={{ color: '#d1d5db' }}>(board/class/subject/topic)</span></label>
            <button onClick={handleAutoGeoTags}
              className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-lg border"
              style={{ color: '#a78bfa', borderColor: 'rgba(167,139,250,0.25)', background: 'rgba(167,139,250,0.08)' }}>
              <Zap size={9} /> Auto-extract
            </button>
          </div>
          <input
            value={form.geo_tags}
            onChange={e => setForm(f => ({ ...f, geo_tags: e.target.value }))}
            placeholder="ahsec/class-12/pcm/physics"
            className="w-full h-10 px-4 rounded-xl text-sm font-mono outline-none"
            style={{ color: '#374151', background: '#f3f4f6', border: '1px solid #e5e7eb' }}
          />
        </div>

        {form.geo_tags && (
          <div>
            <p className="text-xs font-medium mb-2" style={{ color: '#9ca3af' }}>Authority Phrases</p>
            <div className="flex flex-wrap gap-2">
              {[
                form.title && `${form.title}`,
                form.geo_tags && `${form.geo_tags} Study Guide`,
                form.primary_keyword && form.primary_keyword,
                form.geo_tags && `${form.geo_tags} Board Exam`,
                form.geo_tags && `${form.geo_tags} Notes`,
              ].filter(Boolean).map((phrase, i) => (
                <span key={i} className="flex items-center gap-1.5 px-3 py-1 rounded-full text-[10px] font-medium"
                  style={{ background: 'rgba(16,185,129,0.10)', border: '1px solid rgba(16,185,129,0.20)', color: '#34d399' }}>
                  <CheckCircle size={9} /> {phrase}
                </span>
              ))}
            </div>
          </div>
        )}

        <div className="p-4 rounded-xl text-xs space-y-1" style={{ background: 'rgba(124,58,237,0.04)', border: '1px solid rgba(124,58,237,0.08)', color: '#6b7280' }}>
          <p className="font-semibold mb-2" style={{ color: '#7c3aed' }}>GEO Presets</p>
          {['ahsec/class-11/arts', 'ahsec/class-12/science', 'ahsec/class-12/commerce', 'du/degree/bcom', 'du/degree/ba'].map(preset => (
            <button key={preset} onClick={() => setForm(f => ({ ...f, geo_tags: preset }))}
              className="block w-full text-left py-1.5 px-2.5 rounded-lg font-mono text-xs transition-colors"
              style={{ color: '#6b7280' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(124,58,237,0.06)'; e.currentTarget.style.color = '#7c3aed'; }}
              onMouseLeave={e => { e.currentTarget.style.background = ''; e.currentTarget.style.color = '#6b7280'; }}>
              {preset}
            </button>
          ))}
        </div>

        {form.geo_tags && (
          <div className="p-4 rounded-xl text-xs space-y-2" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.12)' }}>
            <p className="font-semibold" style={{ color: '#34d399' }}>Live GEO URL Preview</p>
            <p className="font-mono break-all" style={{ color: '#6b7280' }}>
              syrabit.ai/{form.geo_tags}/{form.seo_slug || 'your-slug'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
