export default function LangToggle({ contentLang, switchLang, variant = 'default', className = '' }) {
  const isCompact = variant === 'compact' || variant === 'floating';
  const enLabel = 'English';
  const btnSize = variant === 'compact' ? 'h-7 px-2.5' : variant === 'floating' ? 'h-8 px-3' : 'h-9 px-3';
  const inactiveClass = isCompact ? 'text-violet-400 hover:bg-violet-500/10' : 'text-violet-600 hover:bg-violet-50';
  const gapClass = isCompact ? '' : ' gap-1.5';
  const wrapperExtras = variant === 'default' ? ' shrink-0' : variant === 'floating' ? ' fixed top-20 right-4 z-30' : '';
  const wrapperStyle = variant === 'floating'
    ? { background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.12)', backdropFilter: 'blur(8px)' }
    : { background: 'rgba(139,92,246,0.08)', border: '1px solid rgba(139,92,246,0.12)' };

  return (
    <div className={`flex items-center gap-1 rounded-xl p-0.5${wrapperExtras} ${className}`} style={wrapperStyle}>
      <button
        onClick={() => switchLang('en')}
        className={`${btnSize} rounded-lg text-xs font-semibold transition-all flex items-center${gapClass} ${
          contentLang === 'en' ? 'text-white bg-violet-600 shadow-sm' : inactiveClass
        }`}
      >
        {enLabel}
      </button>
      <button
        onClick={() => switchLang('as')}
        className={`${btnSize} rounded-lg text-xs font-semibold transition-all flex items-center${gapClass} ${
          contentLang === 'as' ? 'text-white bg-violet-600 shadow-sm' : inactiveClass
        }`}
      >
        অসমীয়া
      </button>
    </div>
  );
}
