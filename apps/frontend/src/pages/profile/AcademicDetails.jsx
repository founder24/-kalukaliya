import { useState, useEffect } from 'react';
import {
  User, Globe, GraduationCap, Layers, Phone, ChevronRight,
  ChevronDown, Check, Target, BookOpen, Zap, Sparkles, Brain, Loader2,
} from 'lucide-react';
import { LogoMark } from '@/components/Logo';
import { StarRating, UsageDots } from './shared';
import { getSubjectsByCourseType, apiClient } from '@/utils/api';
import { toast } from 'sonner';

const COURSE_TYPE_ICONS = {
  'target':   Target,
  'book':     BookOpen,
  'zap':      Zap,
  'sparkles': Sparkles,
  'globe':    Globe,
  'brain':    Brain,
};

const COURSE_TYPE_COLORS = {
  'major': 'from-violet-500/20 to-purple-500/20 border-violet-500/30',
  'minor': 'from-blue-500/20 to-indigo-500/20 border-blue-500/30',
  'sec':   'from-amber-500/20 to-yellow-500/20 border-amber-500/30',
  'vac':   'from-emerald-500/20 to-green-500/20 border-emerald-500/30',
  'mdc':   'from-cyan-500/20 to-teal-500/20 border-cyan-500/30',
  'aec':   'from-rose-500/20 to-pink-500/20 border-rose-500/30',
};

function CourseTypeSelector({ profile, onUpdate }) {
  const [courseTypes, setCourseTypes] = useState([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [selectedCT, setSelectedCT] = useState(profile?.course_type || null);
  const [selectedSubjects, setSelectedSubjects] = useState(profile?.selected_subjects || []);
  const [saving, setSaving] = useState(false);
  const [showSelector, setShowSelector] = useState(false);

  useEffect(() => {
    if (showSelector && profile?.board_id && courseTypes.length === 0) {
      setLoading(true);
      getSubjectsByCourseType(profile.board_id)
        .then((res) => setCourseTypes(res.data))
        .finally(() => setLoading(false));
    }
  }, [showSelector, profile?.board_id]);

  const toggleSubject = (subj) => {
    setSelectedSubjects((prev) => {
      const exists = prev.find((s) => s.id === subj.id);
      if (exists) return prev.filter((s) => s.id !== subj.id);
      return [...prev, { id: subj.id, name: subj.name }];
    });
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await apiClient().patch('/user/profile', {
        course_type: selectedCT,
        stream_name: selectedCT ? selectedCT.charAt(0).toUpperCase() + selectedCT.slice(1) : undefined,
        selected_subjects: selectedSubjects,
      });
      if (onUpdate) onUpdate({ course_type: selectedCT, selected_subjects: selectedSubjects });
      toast.success('Course preferences saved');
      setShowSelector(false);
    } catch {
      toast.error('Failed to save preferences');
    } finally {
      setSaving(false);
    }
  };

  const currentLabel = selectedCT
    ? selectedCT.toUpperCase() + (selectedSubjects.length ? ` (${selectedSubjects.length} subjects)` : '')
    : 'Select course type';

  return (
    <div className="border-b border-border/50">
      <button
        onClick={() => setShowSelector(!showSelector)}
        className="w-full flex items-center gap-3 px-4 py-3.5 hover:bg-accent/30 transition-colors text-left"
        data-testid="edit-field-course_type"
      >
        <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
          style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(139,92,246,0.15)' }}>
          <Layers size={14} style={{ color: 'hsl(var(--primary))' }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-xs text-muted-foreground">Course Type</p>
          <p className="text-sm font-medium text-foreground truncate">{currentLabel}</p>
        </div>
        <ChevronDown size={14} className={`text-muted-foreground/70 flex-shrink-0 transition-transform ${showSelector ? 'rotate-180' : ''}`} />
      </button>

      {showSelector && (
        <div className="px-4 pb-4">
          {loading ? (
            <div className="flex justify-center py-6"><Loader2 size={20} className="animate-spin text-violet-600" /></div>
          ) : (
            <div className="space-y-2 max-h-[380px] overflow-y-auto pr-1">
              {courseTypes.map((ct) => {
                const Icon = COURSE_TYPE_ICONS[ct.icon] || Globe;
                const colorClass = COURSE_TYPE_COLORS[ct.slug] || 'from-violet-500/20 to-purple-500/20 border-violet-500/30';
                const isExpanded = expanded === ct.slug;
                const isSelected = selectedCT === ct.slug;
                const ctSubjectIds = new Set((ct.subjects || []).map((s) => s.id));
                const selectedInThis = selectedSubjects.filter((s) => ctSubjectIds.has(s.id)).length;

                return (
                  <div key={ct.slug}>
                    <button
                      onClick={() => {
                        setSelectedCT(ct.slug);
                        setExpanded(isExpanded ? null : ct.slug);
                      }}
                      className={`w-full flex items-center gap-3 p-3 rounded-xl border transition-all ${
                        isSelected
                          ? 'border-violet-500 bg-violet-500/15'
                          : 'border-border/50 bg-accent/10 hover:border-border'
                      }`}
                    >
                      <div className={`w-8 h-8 rounded-lg bg-gradient-to-br ${colorClass} flex items-center justify-center border shrink-0`}>
                        <Icon size={14} className="text-white" />
                      </div>
                      <div className="text-left flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-semibold text-foreground">{ct.name}</p>
                          {ct.subject_count > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
                              {ct.subject_count}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground truncate">{ct.description}</p>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {selectedInThis > 0 && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-500/20 text-violet-600 font-medium">{selectedInThis}</span>
                        )}
                        {isSelected && <Check size={14} className="text-violet-600" />}
                        <ChevronDown size={12} className={`text-muted-foreground/70 transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                      </div>
                    </button>

                    {isExpanded && (
                      <div className="mt-1 ml-3 space-y-1 pb-1">
                        {(ct.subjects || []).length === 0 ? (
                          <p className="text-muted-foreground text-xs py-2 pl-2">No subjects available yet</p>
                        ) : (
                          (ct.subjects || []).map((subj) => {
                            const isSubjSelected = selectedSubjects.some((s) => s.id === subj.id);
                            return (
                              <button
                                key={subj.id}
                                onClick={() => toggleSubject(subj)}
                                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg border transition-all text-left ${
                                  isSubjSelected
                                    ? 'border-violet-500/50 bg-violet-500/10'
                                    : 'border-transparent bg-accent/5 hover:bg-accent/15'
                                }`}
                              >
                                <div className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                                  isSubjSelected
                                    ? 'bg-violet-500 border-violet-500'
                                    : 'border-muted-foreground/30 bg-transparent'
                                }`}>
                                  {isSubjSelected && <Check size={10} className="text-white" />}
                                </div>
                                <p className={`text-xs font-medium truncate ${isSubjSelected ? 'text-foreground' : 'text-muted-foreground'}`}>
                                  {subj.name}
                                </p>
                              </button>
                            );
                          })
                        )}
                      </div>
                    )}
                  </div>
                );
              })}

              <button
                onClick={handleSave}
                disabled={saving || !selectedCT}
                className="w-full mt-3 py-2.5 rounded-xl text-sm font-semibold text-white bg-violet-600 hover:bg-violet-500 disabled:opacity-40 transition-colors flex items-center justify-center gap-2"
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                {saving ? 'Saving...' : 'Save Course Preferences'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AcademicDetails({ profile, isDegreeProfile, openEdit, onProfileUpdate }) {
  const baseFields = [
    { key: 'name',       label: 'Display Name', value: profile?.name,       icon: User,          placeholder: 'Your full name' },
    { key: 'board_name', label: 'Board',        value: profile?.board_name, icon: Globe,         placeholder: 'AssamBoard division (AHSEC, DEGREE or SEBA)' },
    { key: 'class_name', label: 'Class / Sem',  value: profile?.class_name, icon: GraduationCap, placeholder: 'e.g. Class 12, 2nd Sem' },
  ];

  if (!isDegreeProfile) {
    baseFields.push({
      key: 'stream_name', label: 'Stream', value: profile?.stream_name, icon: Layers, placeholder: 'e.g. Science (PCM), B.Com'
    });
  }

  baseFields.push({ key: 'phone', label: 'Phone', value: profile?.phone, icon: Phone, placeholder: 'Optional phone number' });

  return (
    <>
      <div className="glass-card rounded-2xl overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Academic Details</p>
        </div>
        {baseFields.map(({ key, label, value, icon: Icon, placeholder }, i) => (
          <button
            key={key}
            onClick={() => openEdit(key, label, placeholder)}
            className={`w-full flex items-center gap-3 px-4 py-3.5 hover:bg-accent/30 transition-colors text-left ${
              key !== 'phone' || isDegreeProfile ? 'border-b border-border/50' : ''
            }`}
            data-testid={`edit-field-${key}`}
          >
            <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
              style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(139,92,246,0.15)' }}>
              <Icon size={14} style={{ color: 'hsl(var(--primary))' }} />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="text-sm font-medium text-foreground truncate">{value || `Add ${label.toLowerCase()}`}</p>
            </div>
            <ChevronRight size={14} className="text-muted-foreground/70 flex-shrink-0" />
          </button>
        ))}

        {isDegreeProfile && (
          <CourseTypeSelector profile={profile} onUpdate={onProfileUpdate} />
        )}
      </div>

      <div className="glass-card rounded-2xl overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Preferred AI Models</p>
        </div>
        <div className="p-4 space-y-3">
          {[
            { name: 'Syrabit SLM Instant', badge: 'Fast', stars: 4, dots: 4, dotColor: 'bg-emerald-500', desc: 'Best for quick Q&A, fastest responses', available: true },
            { name: 'Syrabit MLM Versatile', badge: 'Coming Soon', stars: 5, dots: 0, dotColor: 'bg-muted', desc: 'Advanced model launching soon', available: false },
          ].map((m) => (
            <div key={m.name} className="flex items-center gap-3 p-3 rounded-xl"
              style={{ 
                background: m.available ? 'rgba(124,58,237,0.04)' : 'rgba(100,100,100,0.04)', 
                border: m.available ? '1px solid rgba(139,92,246,0.10)' : '1px solid rgba(150,150,150,0.10)',
                opacity: m.available ? 1 : 0.6
              }}>
              <LogoMark size="xs" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground">{m.name}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full"
                    style={{ 
                      background: m.available ? 'rgba(139,92,246,0.12)' : 'rgba(245,158,11,0.12)', 
                      color: m.available ? 'hsl(var(--primary))' : 'rgb(245,158,11)' 
                    }}>
                    {m.badge}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground/60 truncate mt-0.5">{m.desc}</p>
              </div>
              <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
                {m.available && <StarRating value={m.stars} />}
                {m.available && <UsageDots value={m.dots} dotColor={m.dotColor} />}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
