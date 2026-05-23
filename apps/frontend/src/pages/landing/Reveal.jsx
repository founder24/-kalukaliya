import { useEffect, useRef, useState } from 'react';

export default function Reveal({ children, delay = 0, className = '', style: extraStyle, as: Tag = 'div', ...rest }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: '-60px' }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const style = {
    ...extraStyle,
    opacity: visible ? 1 : 0,
    transform: visible ? 'translateY(0)' : 'translateY(28px)',
    transition: `opacity 0.7s cubic-bezier(0.16, 1, 0.3, 1) ${delay}s, transform 0.7s cubic-bezier(0.16, 1, 0.3, 1) ${delay}s`,
    willChange: 'opacity, transform',
  };

  return (
    <Tag ref={ref} className={className} style={style} {...rest}>
      {children}
    </Tag>
  );
}
