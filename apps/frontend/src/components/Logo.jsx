/**
 * Logo — Syrabit.ai brand logo component.
 * Uses the official Syrabit.ai S-mark image.
 */

// ── Size map ────────────────────────────────────────────────────────────────
const SIZE_MAP = {
  xs:    16,
  sm:    28,
  md:    36,
  lg:    52,
  xl:    72,
  '2xl': 96,
};

/**
 * LogoMark — icon only (the S-mark image).
 */
// DPR-aware srcset: tiny slots (≤28px CSS) prefer the 56px asset @1x and
// promote to 144px @2x for retina; larger slots stay on 144px since the
// 56px asset would upscale and look soft.
const _logoSrcSet = (px) =>
  px <= 28
    ? { src: '/logo-56.webp', srcSet: '/logo-56.webp 1x, /logo-144.webp 2x' }
    : { src: '/logo-144.webp', srcSet: '/logo-144.webp 1x, /logo-144.webp 2x' };

export const LogoMark = ({ size = 'md', className = '', style = {} }) => {
  const px = SIZE_MAP[size] ?? SIZE_MAP.md;
  const { src, srcSet } = _logoSrcSet(px);
  return (
    <span className={className} style={{ display: 'inline-flex', flexShrink: 0, ...style }}>
      <img
        src={src}
        srcSet={srcSet}
        alt="Syrabit.ai logo"
        width={px}
        height={px}
        decoding="async"
        style={{ width: px, height: px, borderRadius: px * 0.25, objectFit: 'cover', flexShrink: 0 }}
      />
    </span>
  );
};

/**
 * LogoFull — icon + "Syrabit.ai" wordmark.
 */
export const LogoFull = ({
  size = 'md',
  className = '',
  textClassName = '',
  hideText = false,
  hideIcon = false,
}) => {
  const px = SIZE_MAP[size] ?? SIZE_MAP.md;
  const textSizes = {
    xs:    'text-xs',
    sm:    'text-sm',
    md:    'text-base',
    lg:    'text-xl',
    xl:    'text-2xl',
    '2xl': 'text-3xl',
  };

  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      {!hideIcon && (() => {
        const { src, srcSet } = _logoSrcSet(px);
        return (
          <img
            src={src}
            srcSet={srcSet}
            alt="Syrabit.ai logo"
            width={px}
            height={px}
            decoding="async"
            style={{ width: px, height: px, borderRadius: px * 0.25, objectFit: 'cover', flexShrink: 0 }}
          />
        );
      })()}
      {!hideText && (
        <span
          className={`font-bold tracking-tight ${textSizes[size] ?? textSizes.md} ${textClassName}`}
        >
          Syrabit<span style={{ color: 'hsl(var(--primary))' }}>.ai</span>
        </span>
      )}
    </span>
  );
};

const LOGO_URL = '/logo.webp';
