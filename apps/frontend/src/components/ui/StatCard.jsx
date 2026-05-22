export default function StatCard({ value, label, className = '' }) {
  return (
    <div className={`text-center p-5 rounded-2xl border border-border/40 bg-card/50 ${className}`}>
      <p className="text-3xl font-bold text-violet-600 mb-1">{value}</p>
      <p className="text-sm text-muted-foreground">{label}</p>
    </div>
  );
}
