// React-only exports (Fast Refresh compatible)
// Plain constants live in planConfig.js
import { Star } from 'lucide-react';

export function StarRating({ value = 4, max = 5 }) {
  return (
    <div className="flex items-center gap-0.5">
      {[...Array(max)].map((_, i) => (
        <Star
          key={i}
          size={12}
          className={i < value ? 'text-amber-700 fill-amber-400' : 'text-muted-foreground/70'}
        />
      ))}
    </div>
  );
}

export function UsageDots({ value = 3, max = 4, color = 'bg-primary' }) {
  return (
    <div className="flex items-center gap-1">
      {[...Array(max)].map((_, i) => (
        <div
          key={i}
          className={`w-2 h-2 rounded-full ${i < value ? color : 'bg-muted'}`}
        />
      ))}
    </div>
  );
}
