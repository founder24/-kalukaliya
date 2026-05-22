import { Loader2, Sparkles } from 'lucide-react';

export default function PilotTab({
  piloting, pilotResult, pilotBoard, setPilotBoard,
  pilotClass, setPilotClass, pilotSubject, setPilotSubject,
  pilotChapters, setPilotChapters, handlePilot,
}) {
  return (
    <div className="space-y-5 max-w-lg">
      <div>
        <p className="text-sm font-semibold mb-1" style={{ color: '#374151' }}>Seed Pilot Content</p>
        <p className="text-xs" style={{ color: '#9ca3af' }}>
          Generate full SEO content for the first N chapters of a subject — use this to test the pipeline before running at scale.
        </p>
      </div>

      <div className="space-y-3">
        {[
          { label: 'Board', value: pilotBoard, onChange: setPilotBoard, placeholder: 'AHSEC' },
          { label: 'Class', value: pilotClass, onChange: setPilotClass, placeholder: 'Class 11' },
          { label: 'Subject keyword', value: pilotSubject, onChange: setPilotSubject, placeholder: 'maths / physics / english…' },
        ].map(({ label, value, onChange, placeholder }) => (
          <div key={label}>
            <label className="text-[11px] block mb-1.5" style={{ color: '#6b7280' }}>{label}</label>
            <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
              className="w-full h-10 px-3 rounded-xl text-sm outline-none"
              style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', color: '#374151' }}
            />
          </div>
        ))}
        <div>
          <label className="text-[11px] block mb-1.5" style={{ color: '#6b7280' }}>Chapter limit</label>
          <input type="number" min={1} max={20} value={pilotChapters} onChange={e => setPilotChapters(Number(e.target.value))}
            className="w-full h-10 px-3 rounded-xl text-sm outline-none"
            style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', color: '#374151' }}
          />
        </div>
      </div>

      <button onClick={handlePilot} disabled={piloting || !pilotSubject.trim()}
        className="w-full h-11 rounded-xl text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
        style={{ background: 'linear-gradient(135deg,#7c3aed,#6d28d9)', color: '#fff' }}>
        {piloting ? <><Loader2 size={15} className="animate-spin" /> Generating pilot…</> : <><Sparkles size={15} /> Run Pilot</>}
      </button>

      {pilotResult && !pilotResult.error && (
        <div className="rounded-xl p-4 border" style={{ background: 'rgba(16,185,129,0.07)', borderColor: 'rgba(16,185,129,0.20)' }}>
          <p className="text-xs font-semibold mb-2" style={{ color: '#34d399' }}>Pilot Complete</p>
          {[
            ['Subject', pilotResult.subject],
            ['Chapters processed', pilotResult.chapters_processed],
            ['Topics created', pilotResult.topics_created],
            ['Pages generated', pilotResult.pages_generated],
            ['Errors', pilotResult.errors],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between py-1 border-b" style={{ borderColor: '#f3f4f6' }}>
              <span className="text-xs" style={{ color: '#6b7280' }}>{k}</span>
              <span className="text-xs font-semibold" style={{ color: '#374151' }}>{v ?? '—'}</span>
            </div>
          ))}
        </div>
      )}
      {pilotResult?.error && (
        <div className="rounded-xl p-4 border" style={{ background: 'rgba(239,68,68,0.07)', borderColor: 'rgba(239,68,68,0.20)' }}>
          <p className="text-xs font-semibold" style={{ color: '#f87171' }}>Error: {pilotResult.error}</p>
        </div>
      )}
    </div>
  );
}
