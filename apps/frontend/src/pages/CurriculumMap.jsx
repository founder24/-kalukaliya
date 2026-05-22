import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { ChevronRight, BookOpen, GraduationCap, Layers, FileText, ExternalLink } from 'lucide-react';
import PageMeta from '@/components/seo/PageMeta';
import { PublicNavbar } from '@/components/layout/PublicNavbar';
import { PublicBottomNav } from '@/components/layout/PublicBottomNav';
import { WORKER_API } from '@/utils/api';

function slugify(str = '') {
  return str.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
}

function TreeNode({ label, icon: Icon, count, children, defaultOpen = false, depth = 0 }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={depth === 0 ? 'border border-border/30 rounded-xl overflow-hidden mb-3' : ''}>
      <button
        onClick={() => setOpen((o) => !o)}
        className={`w-full flex items-center gap-3 text-left px-4 py-3 transition-colors
          ${depth === 0 ? 'bg-muted/30 hover:bg-muted/50' : depth === 1 ? 'bg-muted/15 hover:bg-muted/30 pl-4 sm:pl-6 md:pl-8' : 'hover:bg-muted/20 pl-6 sm:pl-8 md:pl-12'}`}
      >
        <ChevronRight
          className={`shrink-0 text-muted-foreground/40 transition-transform duration-200 ${open ? 'rotate-90' : ''}`}
          size={14}
        />
        {Icon && <Icon size={16} className="shrink-0 text-violet-600" />}
        <span className={`flex-1 font-medium ${depth === 0 ? 'text-foreground text-sm' : 'text-foreground/80 text-sm'}`}>{label}</span>
        {count != null && (
          <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">{count}</span>
        )}
      </button>
      {open && <div className="border-t border-border/20">{children}</div>}
    </div>
  );
}

function SubjectNode({ subject, boardSlug, classSlug }) {
  const navigate = useNavigate();
  const chapters = subject.chapters || [];
  const subjectSlug = subject.slug || slugify(subject.name);
  return (
    <TreeNode
      label={subject.name}
      icon={BookOpen}
      count={`${chapters.length} ch`}
      depth={2}
    >
      <div className="pl-8 sm:pl-12 md:pl-16 pr-4 py-2 space-y-1">
        {chapters.length === 0 && (
          <p className="text-xs text-muted-foreground/40 py-2">No chapters yet</p>
        )}
        {chapters.map((ch, idx) => {
          const chSlug = ch.slug || slugify(ch.title || ch.name || `chapter-${idx + 1}`);
          const url = `/${boardSlug}/${classSlug}/${subjectSlug}/${chSlug}`;
          return (
            <button
              key={ch.id || idx}
              onClick={() => navigate(url)}
              className="w-full flex items-center gap-2.5 text-left px-3 py-2 rounded-lg hover:bg-violet-500/[0.06] group transition-colors"
            >
              <FileText size={13} className="shrink-0 text-muted-foreground/40 group-hover:text-violet-600 transition-colors" />
              <span className="flex-1 text-xs text-foreground/60 group-hover:text-foreground transition-colors leading-snug">
                {ch.title || ch.name || `Chapter ${idx + 1}`}
              </span>
              <ExternalLink size={11} className="shrink-0 text-muted-foreground/30 group-hover:text-violet-600 opacity-0 group-hover:opacity-100 transition-all" />
            </button>
          );
        })}
        <button
          onClick={() => navigate(`/subject/${subject.id}`)}
          className="mt-1 w-full text-xs text-violet-600 hover:text-violet-700 py-1.5 rounded-lg hover:bg-violet-500/[0.06] transition-colors text-center"
        >
          Open full subject →
        </button>
      </div>
    </TreeNode>
  );
}

export default function CurriculumMap() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['library-bundle'],
    queryFn: () => axios.get(`${WORKER_API}/content/library-bundle`).then((r) => r.data),
    staleTime: 30 * 60 * 1000,
  });

  const tree = useMemo(() => {
    if (!data) return [];
    const { boards = [], classes = [], streams = [], subjects = [], chapters = [] } = data;

    const chaptersBySubject = {};
    for (const ch of chapters) {
      const sid = ch.subject_id;
      if (sid) {
        if (!chaptersBySubject[sid]) chaptersBySubject[sid] = [];
        chaptersBySubject[sid].push(ch);
      }
    }

    const enrichedSubjects = subjects.map((s) => ({
      ...s,
      chapters: chaptersBySubject[s.id] || [],
    }));

    return boards.map((board) => {
      const boardClasses = classes.filter((c) => c.board_id === board.id);
      return {
        ...board,
        classes: boardClasses.map((cls) => {
          const clsStreams = streams.filter((st) => st.class_id === cls.id);
          return {
            ...cls,
            streams: clsStreams.map((st) => ({
              ...st,
              subjects: enrichedSubjects.filter((sub) => sub.stream_id === st.id),
            })),
          };
        }),
      };
    });
  }, [data]);

  const totalSubjects = data?.subjects?.length ?? 0;
  const totalChapters = data?.chapters?.length ?? 0;

  return (
    <div className="min-h-screen text-foreground bg-background">
      <PageMeta
        title="Curriculum Map — AssamBoard Subject Browser | Syrabit.ai"
        description="Browse the full AssamBoard curriculum: AHSEC Class 11-12 (PCM, PCB, Arts, Commerce), Degree (B.Com, B.A, B.Sc), and SEBA — all subjects and chapters in one place."
        url="https://syrabit.ai/curriculum"
        keywords="AssamBoard syllabus, AHSEC curriculum, SEBA curriculum, Class 11 12 chapters, Degree syllabus Assam, AssamBoard curriculum map"
      />
      <PublicNavbar />

      <div className="max-w-4xl mx-auto px-4 pt-24 pb-16">
        <div className="mb-10">
          <div className="inline-flex items-center gap-2 text-xs font-medium text-violet-600 bg-violet-500/10 border border-violet-500/20 rounded-full px-3 py-1 mb-4">
            <Layers size={12} />
            Full Curriculum
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold text-foreground mb-3">
            Curriculum Map
          </h1>
          <p className="text-muted-foreground text-sm max-w-xl">
            Browse every AssamBoard division (AHSEC, DEGREE, SEBA), class, and subject in the Syrabit library.
            Click any chapter to open its study page.
          </p>
          {!isLoading && (
            <div className="flex gap-4 mt-5">
              {[
                { label: 'Boards', value: tree.length },
                { label: 'Subjects', value: totalSubjects },
                { label: 'Chapters', value: totalChapters },
              ].map(({ label, value }) => (
                <div key={label} className="text-center">
                  <p className="text-2xl font-bold text-violet-600">{value}</p>
                  <p className="text-xs text-muted-foreground">{label}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        {isLoading && (
          <div className="space-y-3">
            {[1, 2].map((i) => (
              <div key={i} className="h-14 rounded-xl bg-muted/50 animate-pulse" />
            ))}
          </div>
        )}

        {error && (
          <div className="text-red-600 text-sm bg-red-500/5 border border-red-500/15 rounded-xl p-4">
            Failed to load curriculum. Please refresh the page.
          </div>
        )}

        {!isLoading && !error && tree.map((board) => (
          <TreeNode
            key={board.id}
            label={board.name}
            icon={GraduationCap}
            count={`${board.classes.reduce((a, c) => a + c.streams.reduce((b, s) => b + s.subjects.length, 0), 0)} subjects`}
            depth={0}
            defaultOpen
          >
            {board.classes.map((cls) => (
              <TreeNode
                key={cls.id}
                label={cls.name}
                icon={Layers}
                count={`${cls.streams.reduce((a, s) => a + s.subjects.length, 0)} subjects`}
                depth={1}
                defaultOpen
              >
                {cls.streams.map((stream) => (
                  stream.subjects.length > 0 && (
                    <div key={stream.id}>
                      <p className="pl-8 pr-4 py-1.5 text-xs font-semibold text-muted-foreground/50 uppercase tracking-widest">
                        {stream.name}
                      </p>
                      {stream.subjects.map((sub) => (
                        <SubjectNode
                          key={sub.id}
                          subject={sub}
                          boardSlug={board.slug}
                          classSlug={cls.slug}
                        />
                      ))}
                    </div>
                  )
                ))}
              </TreeNode>
            ))}
          </TreeNode>
        ))}
      </div>
      <div className="pb-20 md:pb-0" />
      <PublicBottomNav />
    </div>
  );
}
