export default function GlowOrb({ color, size, x, y, blur, opacity = 0.18, animRange = 30, duration = 14 }) {
  const animationName = `glowOrbFloat-${animRange}`;
  return (
    <>
      <style>{`
        @keyframes ${animationName} {
          0%   { transform: translate(0, 0)                       scale(1);    }
          33%  { transform: translate(${animRange}px, -${animRange / 2}px) scale(1.08); }
          66%  { transform: translate(-${animRange / 2}px, ${animRange}px) scale(0.96); }
          100% { transform: translate(0, 0)                       scale(1);    }
        }
        @media (prefers-reduced-motion: reduce) {
          .glow-orb-anim { animation: none !important; }
        }
      `}</style>
      <div
        className="absolute rounded-full pointer-events-none glow-orb-anim"
        style={{
          width: size,
          height: size,
          left: x,
          top: y,
          background: color,
          filter: `blur(${blur}px)`,
          opacity,
          animation: `${animationName} ${duration}s ease-in-out infinite`,
        }}
      />
    </>
  );
}
