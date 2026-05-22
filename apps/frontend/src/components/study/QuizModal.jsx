/**
 * QuizModal — generates and runs an MCQ quiz for the supplied context.
 *
 * Caller supplies one of: `{ context, topic, chapter_ref, subject_name }`.
 * The component fetches questions on mount, walks the user through them,
 * tracks score, and shows a result screen with explanations.
 */
import { useEffect, useState, useCallback } from 'react';
import { X, Check, AlertCircle, Loader2, Trophy } from 'lucide-react';
import { studyApi } from '@/utils/studyApi';
import { toast } from 'sonner';
import { requestReviewPrompt } from '@/components/ReviewPrompt';

export function QuizModal({
  open, onClose,
  context = '', topic = '', chapter_ref = '', subject_name = '',
  count = 7, response_lang = 'en',
}) {
  const [loading, setLoading] = useState(true);
  const [questions, setQuestions] = useState([]);
  const [error, setError] = useState('');
  const [idx, setIdx] = useState(0);
  const [picked, setPicked] = useState(null);
  const [reveal, setReveal] = useState(false);
  const [answers, setAnswers] = useState([]);
  const [done, setDone] = useState(false);

  const fetchQuiz = useCallback(() => {
    setLoading(true); setError(''); setQuestions([]);
    setIdx(0); setPicked(null); setReveal(false);
    setAnswers([]); setDone(false);
    studyApi.generateQuiz({ context, topic, chapter_ref, subject_name, count, response_lang })
      .then((res) => setQuestions(res.questions || []))
      .catch((e) => {
        // Task #615: render the per-actor daily cap with a friendlier copy
        // than a generic toast so students understand it is a daily reset,
        // not a transient burst limit they should retry against.
        if (e?.code === 'quiz_daily_cap') {
          const limit = e?.detail?.limit;
          setError(
            limit
              ? `Daily quiz limit reached (${limit}/day). Try again tomorrow — your quota resets at midnight UTC.`
              : 'Daily quiz limit reached. Try again tomorrow.'
          );
        } else {
          setError(e.message || 'Failed to generate quiz');
        }
      })
      .finally(() => setLoading(false));
  }, [context, topic, chapter_ref, subject_name, count, response_lang]);

  useEffect(() => { if (open) fetchQuiz(); }, [open, fetchQuiz]);

  if (!open) return null;

  const q = questions[idx];
  const submit = () => {
    if (picked === null) return;
    setAnswers((a) => [...a, { idx, picked, correct: picked === q.answer }]);
    setReveal(true);
  };
  const next = () => {
    setReveal(false); setPicked(null);
    if (idx + 1 >= questions.length) {
      setDone(true);
      // Task #652: nudge happy students (>=70% score) to leave a Google
      // review. ReviewPrompt enforces its own 30-day throttling and
      // dismissal rules, so this is safe to fire on every win.
      const finalScore = answers.filter(a => a.correct).length;
      const total = questions.length || 1;
      if (finalScore / total >= 0.7) {
        try { requestReviewPrompt('quiz_high_score'); } catch {}
      }
      return;
    }
    setIdx(idx + 1);
  };

  const score = answers.filter(a => a.correct).length;

  return (
    <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 p-3"
         role="dialog" aria-modal="true" aria-label="Quiz">
      <div className="bg-card text-foreground w-full max-w-2xl rounded-2xl border border-border/60 shadow-xl flex flex-col max-h-[90vh] overflow-hidden">
        <div className="flex items-center justify-between px-5 py-3 border-b border-border/60">
          <div className="text-sm font-semibold flex items-center gap-2">
            <Trophy className="w-4 h-4 text-primary" aria-hidden="true" />
            <span>{done ? 'Quiz complete' : `Quiz me · ${topic || subject_name || 'this topic'}`}</span>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-md hover:bg-muted" aria-label="Close quiz">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          {loading && (
            <div className="flex items-center justify-center min-h-[180px] text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin mr-2" /> Generating questions…
            </div>
          )}

          {error && !loading && (
            <div className="flex flex-col items-center text-center gap-3 py-8">
              <AlertCircle className="w-8 h-8 text-amber-500" />
              <div className="text-sm">{error}</div>
              <button onClick={fetchQuiz} className="text-sm font-medium text-primary hover:underline">Try again</button>
            </div>
          )}

          {!loading && !error && !done && q && (
            <>
              <div className="text-xs text-muted-foreground mb-2">
                Question {idx + 1} of {questions.length} · Score {score}
              </div>
              <div className="text-base font-medium mb-4 leading-snug">{q.q}</div>
              <div className="space-y-2">
                {q.choices.map((c, i) => {
                  const isPicked = picked === i;
                  const isRight = reveal && i === q.answer;
                  const isWrong = reveal && isPicked && i !== q.answer;
                  return (
                    <button
                      key={i}
                      onClick={() => !reveal && setPicked(i)}
                      disabled={reveal}
                      className={[
                        'w-full text-left px-3 py-2.5 rounded-xl border text-sm transition-all',
                        isRight ? 'border-emerald-400 bg-emerald-50 text-emerald-900' :
                        isWrong ? 'border-red-400 bg-red-50 text-red-900' :
                        isPicked ? 'border-primary bg-primary/10' :
                        'border-border/60 hover:bg-muted/40',
                      ].join(' ')}
                    >
                      <span className="inline-flex items-center justify-center w-6 h-6 rounded-md border border-border/60 mr-2 font-mono text-xs">
                        {String.fromCharCode(65 + i)}
                      </span>
                      {c}
                      {isRight && <Check className="inline w-4 h-4 ml-2" />}
                    </button>
                  );
                })}
              </div>
              {reveal && q.explanation && (
                <div className="mt-4 text-xs bg-muted/40 border border-border/40 rounded-lg p-3 text-muted-foreground">
                  <span className="font-semibold text-foreground">Why:</span> {q.explanation}
                </div>
              )}
            </>
          )}

          {!loading && !error && done && (
            <div className="text-center py-6">
              <Trophy className="w-12 h-12 mx-auto text-amber-500 mb-3" />
              <div className="text-2xl font-bold mb-1">{score} / {questions.length}</div>
              <div className="text-sm text-muted-foreground mb-5">
                {score === questions.length ? 'Perfect score!' :
                 score >= Math.ceil(questions.length * 0.7) ? 'Great work — keep going.' :
                 'Review the explanations and retry.'}
              </div>
              <div className="space-y-2 text-left max-h-[300px] overflow-y-auto">
                {answers.map((a, i) => (
                  <div key={i} className={`p-2 rounded-md text-xs border ${a.correct ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'}`}>
                    <div className="font-medium text-foreground/80">{i + 1}. {questions[a.idx].q}</div>
                    <div className="mt-1">
                      <span className={a.correct ? 'text-emerald-700' : 'text-red-700'}>
                        Your answer: {questions[a.idx].choices[a.picked]}
                      </span>
                      {!a.correct && (
                        <div className="text-emerald-700 mt-0.5">
                          Correct: {questions[a.idx].choices[questions[a.idx].answer]}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {!loading && !error && (
          /* Footer is right-aligned now that the "New quiz" regenerate
             button is gone. Each chapter has ONE permanent quiz that is
             generated at chapter-creation time and pinned in the cache,
             so re-rolling questions is no longer a user action — every
             open of this modal serves the same stored quiz. */
          <div className="flex items-center justify-end px-5 py-3 border-t border-border/60">
            {!done ? (
              !reveal ? (
                <button
                  onClick={submit}
                  disabled={picked === null}
                  className="px-4 py-2 rounded-xl text-sm font-semibold bg-primary text-primary-foreground disabled:opacity-50"
                >
                  Submit
                </button>
              ) : (
                <button onClick={next} className="px-4 py-2 rounded-xl text-sm font-semibold bg-primary text-primary-foreground">
                  {idx + 1 >= questions.length ? 'See results' : 'Next'}
                </button>
              )
            ) : (
              <button onClick={onClose} className="px-4 py-2 rounded-xl text-sm font-semibold bg-primary text-primary-foreground">
                Close
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
