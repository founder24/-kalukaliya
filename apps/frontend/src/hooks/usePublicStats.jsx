import { useState, useEffect } from 'react';
import { WORKER_API } from '@/utils/api';

let _cached = null;

export function usePublicStats() {
  const [stats, setStats] = useState(_cached);

  useEffect(() => {
    if (_cached) return;
    fetch(`${WORKER_API}/analytics/public-stats`)
      .then(r => r.json())
      .then(d => {
        _cached = d;
        setStats(d);
      })
      .catch(() => {});
  }, []);

  return stats;
}
