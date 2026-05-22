import { useRef, useState, useEffect, useCallback } from 'react';

export default function ScrollableFilterRow({ children, className = '', ...props }) {
  const scrollRef = useRef(null);
  const [showLeft, setShowLeft] = useState(false);
  const [showRight, setShowRight] = useState(false);

  const updateIndicators = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const { scrollLeft, scrollWidth, clientWidth } = el;
    setShowLeft(scrollLeft > 2);
    setShowRight(scrollLeft + clientWidth < scrollWidth - 2);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateIndicators();
    el.addEventListener('scroll', updateIndicators, { passive: true });
    const ro = new ResizeObserver(updateIndicators);
    ro.observe(el);
    return () => {
      el.removeEventListener('scroll', updateIndicators);
      ro.disconnect();
    };
  }, [updateIndicators]);

  useEffect(() => {
    updateIndicators();
  }, [children, updateIndicators]);

  return (
    <div className="relative" {...props}>
      <div
        ref={scrollRef}
        className={`flex gap-2 overflow-x-auto no-scrollbar ${className}`}
      >
        {children}
      </div>
      {showLeft && (
        <div
          className="pointer-events-none absolute left-0 top-0 bottom-0 w-8 z-10"
          style={{
            background: 'linear-gradient(to right, hsl(var(--background)), transparent)',
          }}
        />
      )}
      {showRight && (
        <div
          className="pointer-events-none absolute right-0 top-0 bottom-0 w-8 z-10"
          style={{
            background: 'linear-gradient(to left, hsl(var(--background)), transparent)',
          }}
        />
      )}
    </div>
  );
}
