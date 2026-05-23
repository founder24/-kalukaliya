import { memo, useState } from 'react';
import { Globe } from 'lucide-react';

function _faviconUrl(domain, size = 32) {
  if (!domain) return null;
  const clean = String(domain).replace(/^https?:\/\//, '').split('/')[0];
  return `https://icons.duckduckgo.com/ip3/${encodeURIComponent(clean)}.ico?size=${size}`;
}

export const SourceFavicon = memo(function SourceFavicon({ domain, size = 16, className = '' }) {
  const [failed, setFailed] = useState(false);
  const url = _faviconUrl(domain, Math.max(16, size * 2));
  const px = `${size}px`;
  if (!url || failed) {
    return (
      <span
        className={`inline-flex items-center justify-center rounded-sm bg-muted text-muted-foreground ${className}`}
        style={{ width: px, height: px }}
        aria-hidden="true"
      >
        <Globe style={{ width: size - 4, height: size - 4 }} />
      </span>
    );
  }
  return (
    <img
      src={url}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={`rounded-sm bg-white object-contain ${className}`}
      style={{ width: px, height: px }}
    />
  );
});
