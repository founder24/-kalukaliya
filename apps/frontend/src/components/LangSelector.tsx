import { type Lang } from '../hooks/useChat';
import clsx from 'clsx';

interface Props {
  lang: Lang;
  setLang: (lang: Lang) => void;
  disabled?: boolean;
}

export function LangSelector({ lang, setLang, disabled }: Props) {
  return (
    <div className="flex gap-1 rounded-lg bg-gray-100 p-1">
      <button
        onClick={() => setLang('en')}
        disabled={disabled}
        className={clsx(
          'rounded-md px-3 py-1 text-sm font-medium transition-all',
          lang === 'en'
            ? 'bg-white text-blue-600 shadow-sm'
            : 'text-gray-500 hover:text-gray-700',
          disabled && 'cursor-not-allowed opacity-50'
        )}
      >
        English
      </button>
      <button
        onClick={() => setLang('as')}
        disabled={disabled}
        className={clsx(
          'rounded-md px-3 py-1 text-sm font-medium transition-all',
          lang === 'as'
            ? 'bg-white text-blue-600 shadow-sm'
            : 'text-gray-500 hover:text-gray-700',
          disabled && 'cursor-not-allowed opacity-50'
        )}
      >
        অসমীয়া
      </button>
    </div>
  );
}
