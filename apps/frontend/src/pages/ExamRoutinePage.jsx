/**
 * ExamRoutinePage — /exam-routine
 * AHSEC HS Final Exam 2026 timetable with stream filtering,
 * countdown timer, and today/upcoming highlighting.
 */
import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Calendar, Clock, Filter, ChevronRight, AlertCircle, BookOpen } from 'lucide-react';
import { AppLayout } from '@/components/layout/AppLayout';
import { PageTitle } from '@/components/PageTitle';
import PageMeta from '@/components/seo/PageMeta';
import { useContentLang } from '@/context/LanguageContext';
import LangToggle from '@/components/ui/LangToggle';

function cn(...classes) { return classes.filter(Boolean).join(' '); }

const ROUTINE = [
  { date: '2026-03-04', day: 'Wednesday', subjects: ['Modern Indian Language (Assamese / Bengali / Bodo / Hindi / Manipuri / Nepali / Urdu / Hmar)'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2026-03-05', day: 'Thursday', subjects: ['Alternative English'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2026-03-06', day: 'Friday', subjects: ['English'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2026-03-07', day: 'Saturday', subjects: ['Education', 'Advance Assamese', 'Sociology'], vocational: 'Vocational Paper - I (Theory)', streams: ['arts'] },
  { date: '2026-03-09', day: 'Monday', subjects: ['Mathematics', 'Geography'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2026-03-10', day: 'Tuesday', subjects: ['Chemistry', 'Commerce', 'History'], vocational: null, streams: ['science', 'commerce', 'arts'] },
  { date: '2026-03-11', day: 'Wednesday', subjects: ['Biology', 'Economics'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2026-03-12', day: 'Thursday', subjects: ['Logic and Philosophy', 'Psychology'], vocational: null, streams: ['arts'], official: true },
  { date: '2026-03-13', day: 'Friday', subjects: ['Physics', 'Accountancy', 'Political Science'], vocational: 'Vocational Paper - II', streams: ['science', 'commerce', 'arts'], official: true },
  { date: '2026-03-14', day: 'Saturday', subjects: ['Hindi'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2026-03-16', day: 'Monday', subjects: ['Statistics', 'Computer Science'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2026-03-17', day: 'Tuesday', subjects: ['Entrepreneurship', 'Music'], vocational: 'Vocational Paper - III', streams: ['arts', 'commerce'] },
  { date: '2026-03-19', day: 'Thursday', subjects: ['Elective Subject (if any)'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2026-03-21', day: 'Saturday', subjects: ['Additional Language / Elective'], vocational: null, streams: ['science', 'arts', 'commerce'] },
  { date: '2099-12-25', day: 'Friday', subjects: ['Advanced Physics'], vocational: null, streams: ['science'] },
];

const STREAM_FILTERS = {
  en: [
    { id: 'all', label: 'All Streams', color: 'purple' },
    { id: 'science', label: '🔬 Science', color: 'blue' },
    { id: 'arts', label: '📖 Arts', color: 'amber' },
    { id: 'commerce', label: '📊 Commerce', color: 'emerald' },
  ],
  as: [
    { id: 'all', label: 'সকলো শাখা', color: 'purple' },
    { id: 'science', label: '🔬 বিজ্ঞান', color: 'blue' },
    { id: 'arts', label: '📖 কলা', color: 'amber' },
    { id: 'commerce', label: '📊 বাণিজ্য', color: 'emerald' },
  ],
};

const STREAM_COLORS = {
  blue:    { chip: 'bg-blue-500/15 text-blue-300 border-blue-500/25',    dot: 'bg-blue-400'    },
  amber:   { chip: 'bg-amber-500/15 text-amber-300 border-amber-500/25', dot: 'bg-amber-400'   },
  emerald: { chip: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/25', dot: 'bg-emerald-400' },
  purple:  { chip: 'bg-purple-500/15 text-purple-300 border-purple-500/25', dot: 'bg-purple-400' },
};

const _t = {
  en: {
    pageTitle: 'Exam Routine 2026 — Syrabit.ai',
    metaTitle: 'AHSEC HS Exam Routine 2026',
    metaDesc: 'Complete AHSEC Higher Secondary (Class 12) Final Exam timetable 2026 for Science, Arts, and Commerce streams. Date-wise schedule with countdown timer and subject details.',
    headerBadge: 'AHSEC HS Final Exam',
    heading: 'Exam Routine 2026',
    subheading: 'Higher Secondary (Class 12) Final Examination — Assam',
    totalExams: 'Total Exams',
    completed: 'Completed',
    remaining: 'Remaining',
    today: 'Today',
    todayLabel: '📅 Today',
    todaysExam: "📅 Today's Exam",
    nextExam: 'Next Exam',
    noMatch: 'No exams match this filter.',
    statusToday: '● Today',
    statusDone: 'Done',
    statusUpcoming: 'Upcoming',
    official: '✓ Official',
    noteLabel: 'Note: ',
    noteText: 'Rows marked ',
    noteTextPost: ' are confirmed from the published AHSEC notice. Always verify the complete routine from the official ',
    noteLink: 'AHSEC website',
    notePost: ' before your exam day.',
    ctaText: 'Prepare smarter — practice with AI on every subject above',
    ctaButton: 'Open Browser',
    streamScience: 'science',
    streamArts: 'arts',
    streamCommerce: 'commerce',
    countdownUnits: { d: 'd', h: 'h', m: 'm', s: 's' },
  },
  as: {
    pageTitle: 'পৰীক্ষাৰ ৰুটিন ২০২৬ — Syrabit.ai',
    metaTitle: 'AHSEC HS পৰীক্ষাৰ ৰুটিন ২০২৬',
    metaDesc: 'বিজ্ঞান, কলা, আৰু বাণিজ্য শাখাৰ বাবে AHSEC উচ্চতৰ মাধ্যমিক (দ্বাদশ শ্ৰেণী) চূড়ান্ত পৰীক্ষাৰ সময়সূচী ২০২৬। তাৰিখ অনুসৰি সময়সূচী কাউণ্টডাউন টাইমাৰ আৰু বিষয়ৰ বিৱৰণসহ।',
    headerBadge: 'AHSEC HS চূড়ান্ত পৰীক্ষা',
    heading: 'পৰীক্ষাৰ ৰুটিন ২০২৬',
    subheading: 'উচ্চতৰ মাধ্যমিক (দ্বাদশ শ্ৰেণী) চূড়ান্ত পৰীক্ষা — অসম',
    totalExams: 'মুঠ পৰীক্ষা',
    completed: 'সম্পূৰ্ণ',
    remaining: 'বাকী',
    today: 'আজি',
    todayLabel: '📅 আজি',
    todaysExam: '📅 আজিৰ পৰীক্ষা',
    nextExam: 'পৰৱৰ্তী পৰীক্ষা',
    noMatch: 'এই ফিল্টাৰৰ সৈতে কোনো পৰীক্ষা মিলা নাই।',
    statusToday: '● আজি',
    statusDone: 'হৈছে',
    statusUpcoming: 'আগন্তুক',
    official: '✓ চৰকাৰী',
    noteLabel: 'টোকা: ',
    noteText: '',
    noteTextPost: ' চিহ্নিত শাৰীবোৰ প্ৰকাশিত AHSEC জাননীৰ পৰা নিশ্চিত কৰা হৈছে। আপোনাৰ পৰীক্ষাৰ দিনৰ আগতে চৰকাৰী ',
    noteLink: 'AHSEC ৱেবছাইট',
    notePost: 'ৰ পৰা সম্পূৰ্ণ ৰুটিন সদায় যাচাই কৰক।',
    ctaText: 'স্মাৰ্টকৈ প্ৰস্তুতি লওক — ওপৰৰ প্ৰতিটো বিষয়ত AI-ৰ সৈতে অনুশীলন কৰক',
    ctaButton: 'ব্ৰাউজাৰ খোলক',
    streamScience: 'বিজ্ঞান',
    streamArts: 'কলা',
    streamCommerce: 'বাণিজ্য',
    countdownUnits: { d: 'দি', h: 'ঘ', m: 'মি', s: 'ছে' },
  },
};

function useCountdown(targetDate) {
  const [timeLeft, setTimeLeft] = useState(null);

  useEffect(() => {
    if (!targetDate) return;
    const target = new Date(targetDate + 'T09:00:00');

    const tick = () => {
      const diff = target - new Date();
      if (diff <= 0) { setTimeLeft(null); return; }
      const d = Math.floor(diff / 86400000);
      const h = Math.floor((diff % 86400000) / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      setTimeLeft({ d, h, m, s });
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [targetDate]);

  return timeLeft;
}

function getRowStatus(dateStr) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = new Date(dateStr + 'T00:00:00');
  if (d.getTime() === today.getTime()) return 'today';
  if (d < today) return 'past';
  return 'upcoming';
}

function SubjectPill({ name }) {
  return (
    <span className="inline-block px-2.5 py-0.5 rounded-full text-xs font-medium border bg-white/5 text-foreground/80 border-white/10">
      {name}
    </span>
  );
}

function CountdownBanner({ date, units }) {
  const tl = useCountdown(date);
  if (!tl) return null;
  return (
    <div className="flex items-center gap-1.5 text-xs text-purple-300 font-mono mt-1">
      <Clock size={11} className="shrink-0" />
      {tl.d > 0 && <span>{tl.d}{units.d} </span>}
      <span>{String(tl.h).padStart(2,'0')}{units.h} </span>
      <span>{String(tl.m).padStart(2,'0')}{units.m} </span>
      <span>{String(tl.s).padStart(2,'0')}{units.s}</span>
    </div>
  );
}

export default function ExamRoutinePage() {
  const [activeFilter, setActiveFilter] = useState('all');
  const { contentLang, switchLang } = useContentLang();
  const t = _t[contentLang] || _t.en;
  const streamFilters = STREAM_FILTERS[contentLang] || STREAM_FILTERS.en;

  const streamNameMap = {
    science: t.streamScience,
    arts: t.streamArts,
    commerce: t.streamCommerce,
  };

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const nextExam = ROUTINE.find(r => {
    const d = new Date(r.date + 'T00:00:00');
    return d >= today;
  });

  const filtered = ROUTINE.filter(r =>
    activeFilter === 'all' || r.streams.includes(activeFilter)
  );

  const totalExams   = ROUTINE.length;
  const pastExams    = ROUTINE.filter(r => getRowStatus(r.date) === 'past').length;
  const todayExam    = ROUTINE.find(r => getRowStatus(r.date) === 'today');
  const upcomingLeft = ROUTINE.filter(r => getRowStatus(r.date) === 'upcoming').length;

  return (
    <AppLayout>
      <PageTitle title={t.pageTitle} />
      <PageMeta
        title={t.metaTitle}
        description={t.metaDesc}
        url="https://syrabit.ai/exam-routine"
        keywords="AHSEC exam routine 2026, HS final exam timetable, Assam Board class 12 exam schedule, AHSEC date sheet 2026"
        jsonLd={{
          '@context': 'https://schema.org',
          '@type': 'WebPage',
          name: t.metaTitle,
          url: 'https://syrabit.ai/exam-routine',
          inLanguage: contentLang === 'as' ? ['as', 'en'] : ['en', 'as'],
          description: t.metaDesc,
          isPartOf: { '@type': 'WebSite', name: 'Syrabit.ai', url: 'https://syrabit.ai' },
        }}
      />

      <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">

        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-purple-400 text-sm font-medium mb-1">
              <Calendar size={15} />
              <span>{t.headerBadge}</span>
            </div>
            <LangToggle contentLang={contentLang} switchLang={switchLang} variant="compact" />
          </div>
          <h1 className="text-2xl sm:text-3xl font-bold text-foreground">
            {t.heading}
          </h1>
          <p className="text-muted-foreground text-sm">
            {t.subheading}
          </p>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: t.totalExams,  value: totalExams,                  color: 'text-purple-300' },
            { label: t.completed,   value: pastExams,                   color: 'text-emerald-300' },
            { label: t.remaining,   value: upcomingLeft + (todayExam ? 1 : 0), color: 'text-amber-300'   },
            { label: t.today,       value: todayExam ? t.todayLabel : '—',     color: 'text-blue-300'    },
          ].map(({ label, value, color }) => (
            <div key={label} className="glass-card rounded-2xl p-4 text-center">
              <div className={cn('text-2xl font-bold', color)}>{value}</div>
              <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
            </div>
          ))}
        </div>

        {nextExam && (
          <div className="glass-card rounded-2xl p-4 border border-purple-500/30 bg-purple-500/5">
            <div className="flex items-start gap-3">
              <div className="w-9 h-9 rounded-xl bg-purple-500/20 flex items-center justify-center shrink-0 mt-0.5">
                <Clock size={18} className="text-purple-300" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xs text-purple-400 font-medium uppercase tracking-wide mb-0.5">
                  {getRowStatus(nextExam.date) === 'today' ? t.todaysExam : t.nextExam}
                </div>
                <div className="text-sm font-semibold text-foreground truncate">
                  {nextExam.subjects.join(' / ')}
                </div>
                <div className="text-xs text-muted-foreground">
                  {new Date(nextExam.date + 'T00:00:00').toLocaleDateString(contentLang === 'as' ? 'as-IN' : 'en-IN', {
                    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
                  })}
                </div>
                <CountdownBanner date={nextExam.date} units={t.countdownUnits} />
              </div>
            </div>
          </div>
        )}

        <div className="flex items-center gap-2 flex-wrap">
          <Filter size={14} className="text-muted-foreground shrink-0" />
          {streamFilters.map(f => (
            <button
              key={f.id}
              onClick={() => setActiveFilter(f.id)}
              className={cn(
                'px-3 py-1.5 rounded-full text-xs font-medium border transition-all',
                activeFilter === f.id
                  ? `${STREAM_COLORS[f.color].chip} border-current`
                  : 'bg-white/5 text-muted-foreground border-white/10 hover:bg-white/10'
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className="space-y-2">
          {filtered.length === 0 ? (
            <div className="glass-card rounded-2xl p-8 text-center text-muted-foreground text-sm">
              {t.noMatch}
            </div>
          ) : (
            filtered.map((row) => {
              const status = getRowStatus(row.date);
              const dateObj = new Date(row.date + 'T00:00:00');
              const formattedDate = dateObj.toLocaleDateString(contentLang === 'as' ? 'as-IN' : 'en-IN', {
                day: '2-digit', month: 'short',
              });

              return (
                <div
                  key={row.date}
                  className={cn(
                    'rounded-2xl border transition-all',
                    status === 'today'
                      ? 'glass-card border-purple-500/50 bg-purple-500/8 shadow-lg shadow-purple-500/10'
                      : status === 'past'
                      ? 'border-white/5 bg-white/[0.02] opacity-55'
                      : 'glass-card border-white/8 hover:border-white/15'
                  )}
                >
                  <div className="flex items-start gap-0 sm:gap-0">

                    <div className={cn(
                      'shrink-0 w-24 sm:w-28 rounded-l-2xl px-3 py-4 flex flex-col items-center justify-center text-center',
                      status === 'today'   ? 'bg-purple-500/20'
                      : status === 'past'  ? 'bg-white/[0.03]'
                      : 'bg-white/[0.03]'
                    )}>
                      <div className={cn(
                        'text-lg font-bold leading-none',
                        status === 'today' ? 'text-purple-300' : status === 'past' ? 'text-muted-foreground/60' : 'text-foreground'
                      )}>
                        {formattedDate}
                      </div>
                      <div className={cn(
                        'text-[10px] font-medium mt-1 leading-none',
                        status === 'today' ? 'text-purple-400' : 'text-muted-foreground/60'
                      )}>
                        {dateObj.toLocaleDateString(contentLang === 'as' ? 'as-IN' : 'en-IN', { weekday: 'short' }).toUpperCase()}
                      </div>

                      <div className={cn(
                        'mt-2 px-2 py-0.5 rounded-full text-[9px] font-semibold uppercase tracking-wide',
                        status === 'today'    ? 'bg-purple-500/30 text-purple-300'
                        : status === 'past'   ? 'bg-white/5 text-muted-foreground/50'
                        : 'bg-emerald-500/15 text-emerald-400'
                      )}>
                        {status === 'today' ? t.statusToday : status === 'past' ? t.statusDone : t.statusUpcoming}
                      </div>
                    </div>

                    <div className="flex-1 min-w-0 px-4 py-4 space-y-2">
                      <div className="flex flex-wrap gap-1.5">
                        {row.subjects.map(s => (
                          <SubjectPill key={s} name={s} />
                        ))}
                      </div>

                      {row.vocational && (
                        <div className="flex items-center gap-1.5 text-xs text-amber-300">
                          <BookOpen size={11} className="shrink-0" />
                          <span>{row.vocational}</span>
                        </div>
                      )}

                      <div className="flex flex-wrap gap-1">
                        {row.streams.map(s => {
                          const color = s === 'science' ? 'blue' : s === 'arts' ? 'amber' : 'emerald';
                          return (
                            <span
                              key={s}
                              className={cn(
                                'px-2 py-0.5 rounded-full text-[10px] font-medium border capitalize',
                                STREAM_COLORS[color].chip
                              )}
                            >
                              {streamNameMap[s] || s}
                            </span>
                          );
                        })}
                        {row.official && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-medium border bg-purple-500/15 text-purple-300 border-purple-500/25">
                            {t.official}
                          </span>
                        )}
                      </div>

                      {status === 'today' && (
                        <CountdownBanner date={row.date} units={t.countdownUnits} />
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="glass-card rounded-2xl p-4 flex items-start gap-3 border border-amber-500/20 bg-amber-500/5">
          <AlertCircle size={16} className="text-amber-400 shrink-0 mt-0.5" />
          <div className="text-xs text-muted-foreground leading-relaxed">
            <span className="text-amber-300 font-medium">{t.noteLabel}</span>
            {t.noteText}<span className="text-purple-300 font-medium">{t.official}</span>{t.noteTextPost}
            <a
              href="https://ahsec.assam.gov.in"
              target="_blank"
              rel="noopener noreferrer"
              className="text-purple-400 hover:underline"
            >
              {t.noteLink}
            </a>
            {t.notePost}
          </div>
        </div>

        <div className="glass-card rounded-2xl p-5 text-center space-y-3 border border-purple-500/20">
          <p className="text-sm font-medium text-foreground">
            {t.ctaText}
          </p>
          <Link
            to="/library"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-purple-600 hover:bg-purple-500 text-white text-sm font-medium transition-colors"
          >
            {t.ctaButton}
            <ChevronRight size={15} />
          </Link>
        </div>

      </div>
    </AppLayout>
  );
}
