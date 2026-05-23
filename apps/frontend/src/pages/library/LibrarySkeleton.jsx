export default function LibrarySkeleton() {
  const pulse = { background: 'hsl(var(--muted))' };
  const pulseDim = { background: 'hsl(var(--muted) / 0.7)' };
  return (
    <div className="flex flex-col h-full w-full overflow-hidden animate-pulse">
      <div
        className="sticky top-0 shrink-0 w-full"
        style={{
          background: 'var(--background)',
          borderBottom: '1px solid rgba(139,92,246,0.08)',
        }}
      >
        <div className="w-full max-w-6xl mx-auto px-4 md:px-6 pt-5 pb-3 space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div className="space-y-2 min-w-0">
              <div className="h-6 w-48 rounded-lg" style={pulse} />
              <div className="h-3 w-32 rounded" style={pulseDim} />
            </div>
            <div className="h-9 w-[152px] rounded-xl shrink-0" style={pulse} />
          </div>
          <div className="h-11 w-full rounded-xl" style={pulseDim} />
        </div>
      </div>
      <div className="flex-1 overflow-hidden">
        <div className="w-full max-w-6xl mx-auto px-4 md:px-6 py-5">
          <div className="flex gap-2 pb-4 overflow-hidden">
            {[60, 80, 72, 68, 90, 70].map((w, i) => (
              <div key={i} className="h-8 rounded-full flex-shrink-0" style={{ width: w, ...pulseDim }} />
            ))}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            {[...Array(6)].map((_, i) => (
              <div
                key={i}
                className="rounded-2xl"
                style={{
                  background: 'var(--card)',
                  border: '1px solid rgba(139,92,246,0.10)',
                  minHeight: '420px',
                }}
              >
                <div className="h-9 rounded-t-2xl" style={pulseDim} />
                <div className="px-3 sm:px-4 pt-3 pb-2 space-y-2.5">
                  <div className="flex items-start gap-3">
                    <div className="w-10 h-10 rounded-xl flex-shrink-0" style={pulse} />
                    <div className="flex-1 space-y-1.5">
                      <div className="h-4 rounded w-3/4" style={pulse} />
                      <div className="h-3 rounded w-1/2" style={pulseDim} />
                    </div>
                  </div>
                  <div className="h-3 rounded w-full" style={pulseDim} />
                  <div className="h-3 rounded w-5/6" style={pulseDim} />
                </div>
                <div className="mx-3 mb-3 rounded-xl overflow-hidden" style={{ background: 'rgba(139,92,246,0.03)', border: '1px solid rgba(139,92,246,0.08)' }}>
                  <div className="h-7" style={pulseDim} />
                  <div className="h-9 border-t border-white/5" style={pulseDim} />
                  <div className="h-9 border-t border-white/5" style={pulseDim} />
                  <div className="h-9 border-t border-white/5" style={pulseDim} />
                </div>
                <div className="grid grid-cols-2 gap-1.5 px-3 pb-3 pt-2.5" style={{ borderTop: '1px solid hsl(var(--border) / 0.3)' }}>
                  {[...Array(4)].map((_, j) => (
                    <div key={j} className="h-11 sm:h-9 rounded-lg" style={pulseDim} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
