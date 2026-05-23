import { memo } from 'react';

const FilterChip = memo(function FilterChip({ chip, isActive, onClick }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={isActive}
      className="flex-shrink-0 px-4 py-1.5 rounded-full text-sm transition-all duration-200 active:scale-95"
      style={
        isActive
          ? {
              color: '#fff',
              fontWeight: 600,
              background: 'linear-gradient(135deg, #7c3aed, #8b5cf6)',
              boxShadow: '0 4px 20px rgba(139,92,246,0.40), 0 0 0 1px rgba(255,255,255,0.06) inset',
            }
          : {
              color: 'hsl(var(--muted-foreground))',
              fontWeight: 500,
              background: 'rgba(139,92,246,0.05)',
              border: '1px solid rgba(139,92,246,0.14)',
            }
      }
      onMouseEnter={e => {
        if (!isActive) {
          e.currentTarget.style.background = 'rgba(139,92,246,0.10)';
          e.currentTarget.style.borderColor = 'rgba(139,92,246,0.22)';
          e.currentTarget.style.color = 'hsl(var(--foreground))';
        }
      }}
      onMouseLeave={e => {
        if (!isActive) {
          e.currentTarget.style.background = 'rgba(139,92,246,0.05)';
          e.currentTarget.style.borderColor = 'rgba(139,92,246,0.14)';
          e.currentTarget.style.color = 'hsl(var(--muted-foreground))';
        }
      }}
      data-testid="library-filter-chip"
    >
      {chip.label}
    </button>
  );
});

export default FilterChip;
