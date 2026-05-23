export default function StatCard({ icon: Icon, label, value, color = '#e8e8e8', sub }) {
  return (
    <div className="rounded-xl p-4 border" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
      <Icon size={15} style={{ color, marginBottom: 8 }} />
      <p className="text-2xl font-bold" style={{ color }}>{value ?? '—'}</p>
      <p className="text-[11px] mt-0.5" style={{ color: '#9ca3af' }}>{label}</p>
      {sub && <p className="text-[10px] mt-1" style={{ color: '#d1d5db' }}>{sub}</p>}
    </div>
  );
}
