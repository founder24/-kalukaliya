/**
 * HighlightSavePopover — global text-selection helper.
 *
 * Listens for selection changes inside an opt-in container (any element
 * carrying `data-savable="true"`). When the user finishes a selection
 * of >= 6 chars, a small popover appears anchored above the selection
 * with "Save to Notebook" and "Quiz me" actions.
 *
 * Mounting: render <HighlightSavePopover sourceUrl=… sourceTitle=…
 * chapterRef=… subjectName=… /> once on any reader/chapter page.
 */
import { useEffect, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Bookmark, HelpCircle, Check } from 'lucide-react';
import { studyApi } from '@/utils/studyApi';
import { toast } from 'sonner';
import { QuizModal } from './QuizModal';

function _isInsideSavable(node) {
  let el = node && node.nodeType === 3 ? node.parentElement : node;
  while (el) {
    if (el.getAttribute) {
      const val = el.getAttribute('data-savable');
      if (val === 'false') return false;
      if (val === 'true') return true;
    }
    el = el.parentElement;
  }
  return false;
}

export function HighlightSavePopover({
  sourceUrl = '', sourceTitle = '', chapterRef = '', subjectName = '', hideQuiz = false, hideSave = false,
}) {
  const [pos, setPos] = useState(null);     // {x,y,text}
  const [saved, setSaved] = useState(false);
  const [quizOpen, setQuizOpen] = useState(false);
  const [quizCtx, setQuizCtx] = useState('');

  useEffect(() => {
    let raf = null;
    const handler = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const sel = window.getSelection && window.getSelection();
        if (!sel || sel.isCollapsed) { setPos(null); return; }
        const text = sel.toString().trim();
        if (text.length < 6 || text.length > 4000) { setPos(null); return; }
        const range = sel.getRangeAt(0);
        if (!_isInsideSavable(range.commonAncestorContainer)) { setPos(null); return; }
        const r = range.getBoundingClientRect();
        if (!r || (r.width === 0 && r.height === 0)) { setPos(null); return; }
        setSaved(false);
        setPos({
          x: Math.min(window.innerWidth - 220, Math.max(8, r.left + r.width / 2 - 110)),
          y: Math.max(8, r.top - 48 + window.scrollY),
          text,
        });
      });
    };
    document.addEventListener('selectionchange', handler);
    return () => {
      document.removeEventListener('selectionchange', handler);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

  const onSave = useCallback(async () => {
    if (!pos) return;
    try {
      await studyApi.createNote({
        text: pos.text, source_url: sourceUrl, source_title: sourceTitle,
        chapter_ref: chapterRef, tags: [],
      });
      setSaved(true);
      toast.success('Saved to Notebook', { duration: 1800 });
      setTimeout(() => setPos(null), 900);
    } catch (e) {
      toast.error(e.message || 'Could not save note');
    }
  }, [pos, sourceUrl, sourceTitle, chapterRef]);

  const onQuiz = useCallback(() => {
    if (!pos) return;
    setQuizCtx(pos.text);
    setQuizOpen(true);
    setPos(null);
  }, [pos]);

  const showSave = !hideSave;
  const showQuiz = !hideQuiz;

  return (
    <>
      {typeof document !== 'undefined' && pos && (showSave || showQuiz) && createPortal(
        <div
          className="fixed z-[110] flex items-center gap-1 rounded-xl border border-border/60 bg-card shadow-lg px-1 py-1"
          style={{ left: pos.x, top: pos.y }}
          onMouseDown={(e) => e.preventDefault()}
        >
          {showSave && (
            <button
              onClick={onSave}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium hover:bg-muted"
            >
              {saved ? <Check className="w-3.5 h-3.5 text-emerald-600" /> :
                       <Bookmark className="w-3.5 h-3.5" />}
              {saved ? 'Saved' : 'Save'}
            </button>
          )}
          {showSave && showQuiz && <div className="w-px h-5 bg-border/60" />}
          {showQuiz && (
            <button
              onClick={onQuiz}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium hover:bg-muted"
            >
              <HelpCircle className="w-3.5 h-3.5" /> Quiz me
            </button>
          )}
        </div>,
        document.body,
      )}
      {typeof document !== 'undefined' && createPortal(
        <QuizModal
          open={quizOpen} onClose={() => setQuizOpen(false)}
          context={quizCtx} subject_name={subjectName}
          chapter_ref={chapterRef} count={5}
        />,
        document.body,
      )}
    </>
  );
}
