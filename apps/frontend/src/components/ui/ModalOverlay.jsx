import { X } from 'lucide-react';

export default function ModalOverlay({
  open = true,
  onClose,
  title,
  description,
  children,
  footer,
  maxWidth = 'max-w-sm',
  borderColor = 'rgba(139,92,246,0.20)',
  backdropOpacity = '0.65',
  showCloseButton = true,
  header,
  containerClassName = '',
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{
        background: `rgba(0,0,0,${backdropOpacity})`,
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && onClose) onClose();
      }}
    >
      <div
        className={`w-full ${maxWidth} rounded-2xl p-5 space-y-4 ${containerClassName}`}
        style={{
          background: 'hsl(var(--card))',
          border: `1px solid ${borderColor}`,
          boxShadow: '0 24px 80px rgba(0,0,0,0.45)',
        }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        {header || (title && (
          <div className="flex items-start justify-between">
            <div>
              <h3 id="modal-title" className="font-semibold text-foreground">
                {title}
              </h3>
              {description && (
                <p className="text-sm text-muted-foreground mt-0.5">
                  {description}
                </p>
              )}
            </div>
            {showCloseButton && onClose && (
              <button
                onClick={onClose}
                className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent/40 transition-colors"
                aria-label="Close dialog"
              >
                <X size={16} aria-hidden="true" />
              </button>
            )}
          </div>
        ))}
        {children}
        {footer && <div className="flex gap-2 pt-1">{footer}</div>}
      </div>
    </div>
  );
}
