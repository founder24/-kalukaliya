import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, ChevronLeft, Globe, BookOpen, GraduationCap, FlaskConical, Leaf, Feather, Loader2, Sparkles, Check, AlertCircle, LogOut, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/context/AuthContext';
import { getBoards, getClasses, getStreams, saveOnboarding } from '@/utils/api';
import { isDegreeBoard, streamStepHint } from '@/utils/courseTypes';
import { toast } from 'sonner';

import { LogoMark } from '@/components/Logo';

const STREAM_ICONS = {
  'Science (PCM)': FlaskConical,
  'Science (PCB)': Leaf,
  'Arts':          Feather,
  'Commerce':      GraduationCap,
  'B.Com':         Feather,
  'B.A':           BookOpen,
  'B.Sc':          FlaskConical,
};

const STREAM_COLORS = {
  'Science (PCM)': 'from-cyan-500/20 to-blue-500/20 border-cyan-500/30',
  'Science (PCB)': 'from-emerald-500/20 to-green-500/20 border-emerald-500/30',
  'Arts':          'from-amber-500/20 to-orange-500/20 border-amber-500/30',
  'Commerce':      'from-yellow-500/20 to-amber-500/20 border-yellow-500/30',
  'B.Com':         'from-amber-500/20 to-yellow-500/20 border-amber-500/30',
  'B.A':           'from-rose-500/20 to-pink-500/20 border-rose-500/30',
  'B.Sc':          'from-cyan-500/20 to-teal-500/20 border-cyan-500/30',
};

export default function OnboardingPage() {
  const navigate = useNavigate();
  const { user, authChecked, refreshUser, updateUser, justAuthenticated, logout } = useAuth();
  const [step, setStep] = useState(0);

  const [boards, setBoards] = useState([]);
  const [classes, setClasses] = useState([]);
  const [streams, setStreams] = useState([]);

  const [selectedBoard, setSelectedBoard] = useState(null);
  const [selectedClass, setSelectedClass] = useState(null);
  const [selectedStream, setSelectedStream] = useState(null);

  const isDegreeSel = isDegreeBoard(selectedBoard?.name);
  const totalSteps = isDegreeSel ? 2 : 3;
  const STEPS = isDegreeSel ? ['Board', 'Semester'] : ['Board', 'Class', 'Stream'];
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [fetchError, setFetchError] = useState(null);

  useEffect(() => {
    if (!authChecked) return;
    if (!user && !justAuthenticated.current) {
      navigate('/login', { replace: true });
      return;
    }
    if (user?.onboarding_done) navigate('/library', { replace: true });
  }, [user, authChecked, navigate, justAuthenticated]);

  const loadBoards = () => {
    setFetchError(null);
    setLoading(true);
    getBoards()
      .then((res) => setBoards(res.data))
      .catch(() => setFetchError('boards'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadBoards();
  }, []);

  useEffect(() => {
    if (selectedBoard) {
      setFetchError(null);
      setLoading(true);
      getClasses(selectedBoard.id)
        .then((res) => setClasses(res.data))
        .catch(() => setFetchError('classes'))
        .finally(() => setLoading(false));
    }
  }, [selectedBoard]);

  useEffect(() => {
    if (selectedClass && !isDegreeSel) {
      setFetchError(null);
      setLoading(true);
      getStreams(selectedClass.id)
        .then((res) => setStreams(res.data))
        .catch(() => setFetchError('streams'))
        .finally(() => setLoading(false));
    }
  }, [selectedClass, isDegreeSel]);

  const handleRetry = () => {
    if (fetchError === 'boards') loadBoards();
    else if (fetchError === 'classes' && selectedBoard) {
      setFetchError(null);
      setLoading(true);
      getClasses(selectedBoard.id)
        .then((res) => setClasses(res.data))
        .catch(() => setFetchError('classes'))
        .finally(() => setLoading(false));
    } else if (fetchError === 'streams' && selectedClass) {
      setFetchError(null);
      setLoading(true);
      getStreams(selectedClass.id)
        .then((res) => setStreams(res.data))
        .catch(() => setFetchError('streams'))
        .finally(() => setLoading(false));
    }
  };

  const goNext = () => setStep((s) => s + 1);
  const goBack = () => setStep((s) => s - 1);

  const isLastStep = step === totalSteps - 1;

  const handleFinish = async () => {
    setSaving(true);
    try {
      const onboardingData = {
        board_id: selectedBoard.id,
        board_name: selectedBoard.name,
        class_id: selectedClass.id,
        class_name: selectedClass.name,
      };
      if (!isDegreeSel && selectedStream) {
        onboardingData.stream_id = selectedStream.id;
        onboardingData.stream_name = selectedStream.name;
      }
      await saveOnboarding(onboardingData);
      localStorage.setItem('syrabit:onboarding', JSON.stringify(onboardingData));
      // In-tab signal — `storage` events only fire in OTHER tabs, so the
      // LibraryPage in this tab needs an explicit notification to refetch
      // its boot bundle for the newly-selected board.
      try { window.dispatchEvent(new Event('syrabit:onboarding-updated')); } catch {}
      updateUser({ onboarding_done: true });
      try {
        await refreshUser();
      } catch {}
      toast.success('Setup complete! Welcome to Syrabit.ai');
      navigate('/library', { replace: true });
    } catch (err) {
      toast.error('Failed to save preferences');
    } finally {
      setSaving(false);
    }
  };

  const handleLogout = async () => {
    try {
      await logout();
    } catch {}
    navigate('/login', { replace: true });
  };

  const canProceed = [
    selectedBoard !== null,
    selectedClass !== null,
    !isDegreeSel ? selectedStream !== null : true,
  ];

  const getStreamIcon = (name) => STREAM_ICONS[name] || FlaskConical;
  const getStreamColor = (name) => STREAM_COLORS[name] || 'from-violet-500/20 to-purple-500/20 border-violet-500/30';

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4 overflow-y-auto">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="flex justify-center mb-4">
            <LogoMark size="xl" className="anim-float" />
          </div>
          <h1 className="text-2xl font-semibold text-foreground mb-1">Set Up Your Profile</h1>
          <p className="text-muted-foreground text-sm">Tell us about your studies so we can personalize your experience</p>
        </div>

        <nav aria-label="Onboarding progress">
          <ol className="flex items-center gap-1 sm:gap-2 mb-8 list-none p-0 m-0">
            {STEPS.map((label, i) => {
              const status = i < step ? 'completed' : i === step ? 'current' : 'upcoming';
              return (
                <li
                  key={label}
                  className="flex items-center gap-1 sm:gap-2 flex-1 min-w-0"
                  aria-label={`Step ${i + 1} of ${totalSteps}: ${label} – ${status}`}
                  aria-current={i === step ? 'step' : undefined}
                >
                  <div
                    aria-hidden="true"
                    className={`flex items-center justify-center w-7 h-7 rounded-full text-xs font-semibold transition-colors flex-shrink-0 ${
                      i < step ? 'bg-emerald-500 text-white' :
                      i === step ? 'bg-violet-600 text-white' :
                      'bg-muted text-muted-foreground'
                    }`}
                  >
                    {i < step ? <Check size={14} /> : i + 1}
                  </div>
                  <span aria-hidden="true" className={`text-xs font-medium truncate hidden min-[360px]:inline ${
                    i <= step ? 'text-foreground' : 'text-muted-foreground/50'
                  }`}>{label}</span>
                  {i < STEPS.length - 1 && (
                    <div aria-hidden="true" className={`h-px flex-1 min-w-[8px] ${
                      i < step ? 'bg-emerald-500/50' : 'bg-border'
                    }`} />
                  )}
                </li>
              );
            })}
          </ol>
        </nav>

        <div className="glass-card rounded-2xl p-6 min-h-[300px]">
            <div>
              {fetchError && (
                <div className="flex flex-col items-center justify-center py-8 gap-3">
                  <AlertCircle size={32} className="text-red-500" />
                  <p className="text-muted-foreground text-sm text-center">Failed to load data. Check your connection and try again.</p>
                  <Button
                    onClick={handleRetry}
                    className="bg-violet-600 hover:bg-violet-500 text-white text-sm"
                  >
                    <RefreshCw size={14} className="mr-1.5" /> Retry
                  </Button>
                </div>
              )}

              {!fetchError && step === 0 && (
                <div>
                  <h2 className="text-lg font-semibold text-foreground mb-1">Select your Division</h2>
                  <p className="text-muted-foreground text-sm mb-4">Choose your division under AssamBoard.</p>
                  <div className="mb-4 px-3 py-2 rounded-lg bg-violet-500/10 border border-violet-500/20 flex items-center gap-2">
                    <span className="text-xs font-bold text-violet-600 uppercase tracking-widest">AssamBoard</span>
                  </div>
                  {loading ? (
                    <div className="flex justify-center py-8"><Loader2 size={24} className="animate-spin text-violet-500" /></div>
                  ) : (
                    <div className="space-y-3">
                      {boards.map((board) => (
                        <button
                          key={board.id}
                          onClick={() => setSelectedBoard(board)}
                          className={`w-full flex items-center gap-4 p-4 rounded-xl border transition-all ${
                            selectedBoard?.id === board.id
                              ? 'border-violet-500 bg-violet-500/10'
                              : 'border-border bg-muted/30 hover:border-border/80 hover:bg-muted/50'
                          }`}
                          data-testid={`board-option-${board.id}`}
                        >
                          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-600/20 to-violet-800/20 flex items-center justify-center">
                            <Globe size={20} className="text-violet-600" />
                          </div>
                          <div className="text-left">
                            <p className="text-foreground font-semibold">{board.name}</p>
                            <p className="text-muted-foreground text-xs">{board.description}</p>
                          </div>
                          {selectedBoard?.id === board.id && (
                            <div className="ml-auto"><Check size={18} className="text-violet-600" /></div>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {!fetchError && step === 1 && (
                <div>
                  <h2 className="text-lg font-semibold text-foreground mb-1">Select your {isDegreeSel ? 'Semester' : 'Class'}</h2>
                  <p className="text-muted-foreground text-sm mb-6">{isDegreeSel ? 'Which semester are you currently in?' : 'Which class are you currently in?'}</p>
                  {loading ? (
                    <div className="flex justify-center py-8"><Loader2 size={24} className="animate-spin text-violet-500" /></div>
                  ) : (
                    <div className="grid grid-cols-2 gap-3">
                      {classes.map((cls) => (
                        <button
                          key={cls.id}
                          onClick={() => setSelectedClass(cls)}
                          className={`flex flex-col items-center gap-3 p-5 rounded-xl border transition-all ${
                            selectedClass?.id === cls.id
                              ? 'border-violet-500 bg-violet-500/10'
                              : 'border-border bg-muted/30 hover:border-border/80'
                          }`}
                          data-testid={`class-option-${cls.id}`}
                        >
                          <div className="w-10 h-10 rounded-xl bg-muted flex items-center justify-center">
                            {cls.name.includes('11') || cls.name.includes('2nd') ? (
                              <BookOpen size={20} className="text-violet-600" />
                            ) : (
                              <GraduationCap size={20} className="text-violet-600" />
                            )}
                          </div>
                          <div className="text-center">
                            <p className="text-foreground font-semibold text-sm">{cls.name}</p>
                            <p className="text-muted-foreground text-xs">{cls.description}</p>
                          </div>
                          {selectedClass?.id === cls.id && <Check size={16} className="text-violet-600" />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {!fetchError && step === 2 && !isDegreeSel && (
                <div>
                  <h2 className="text-lg font-semibold text-foreground mb-1">Select your Stream</h2>
                  <p className="text-muted-foreground text-sm mb-6">{streamStepHint(selectedBoard?.name)}</p>
                  {loading ? (
                    <div className="flex justify-center py-8"><Loader2 size={24} className="animate-spin text-violet-500" /></div>
                  ) : (
                    <div className="space-y-3">
                      {streams.map((stream) => {
                        const Icon = getStreamIcon(stream.name);
                        const colorClass = getStreamColor(stream.name);
                        return (
                          <button
                            key={stream.id}
                            onClick={() => setSelectedStream(stream)}
                            className={`w-full flex items-center gap-4 p-4 rounded-xl border transition-all ${
                              selectedStream?.id === stream.id
                                ? 'border-violet-500 bg-violet-500/10'
                                : 'border-border bg-muted/30 hover:border-border/80'
                            }`}
                            data-testid={`stream-option-${stream.id}`}
                          >
                            <div className={`w-10 h-10 rounded-xl bg-gradient-to-br ${colorClass} flex items-center justify-center border`}>
                              <Icon size={18} className="text-foreground/70" />
                            </div>
                            <div className="text-left">
                              <p className="text-foreground font-semibold">{stream.name}</p>
                              <p className="text-muted-foreground text-xs">{stream.description}</p>
                            </div>
                            {selectedStream?.id === stream.id && <div className="ml-auto"><Check size={18} className="text-violet-600" /></div>}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
        </div>

        <div className="flex items-center justify-between mt-6">
          {step > 0 ? (
            <Button
              variant="ghost"
              onClick={goBack}
              className="text-muted-foreground hover:text-foreground hover:bg-muted"
              data-testid="onboarding-back-button"
            >
              <ChevronLeft size={16} className="mr-1" /> Back
            </Button>
          ) : (
            <Button
              variant="ghost"
              onClick={handleLogout}
              className="text-muted-foreground/60 hover:text-foreground hover:bg-muted text-xs"
            >
              <LogOut size={14} className="mr-1" /> Logout
            </Button>
          )}

          {!isLastStep ? (
            <Button
              onClick={goNext}
              disabled={!canProceed[step] || !!fetchError}
              className="bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-40"
              data-testid="onboarding-next-button"
            >
              Next <ChevronRight size={16} className="ml-1" />
            </Button>
          ) : (
            <Button
              onClick={handleFinish}
              disabled={!canProceed[step] || saving || !!fetchError}
              className="bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-40"
              data-testid="onboarding-finish-button"
            >
              {saving ? <Loader2 size={16} className="animate-spin mr-1" /> : <Sparkles size={16} className="mr-1" />}
              {saving ? 'Saving...' : 'Start Studying'}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
