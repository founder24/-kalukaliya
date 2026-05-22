import {
  Rocket, Library, GitBranch, BookMarked, MessageSquareText, Users,
  Sparkles,
} from 'lucide-react';
import Reveal from './Reveal';

const SECTIONS = {
  en: [
    { icon: Rocket, title: 'Our Mission', desc: 'Syrabit.ai is an AI-powered educational platform designed to deliver syllabus-aligned, reliable, and context-aware learning for AHSEC, SEBA, and Degree students across Assam.', gradient: 'linear-gradient(135deg,#7c3aed,#8b5cf6)', glow: 'rgba(139,92,246,0.18)', border: 'rgba(139,92,246,0.15)' },
    { icon: Library, title: 'Platform Architecture', desc: 'Academic content is organized into structured subject cards that function as dedicated knowledge hubs — combining structured navigation with intelligent AI assistance for an interactive, verifiable learning experience.', gradient: 'linear-gradient(135deg,#2563eb,#06b6d4)', glow: 'rgba(59,130,246,0.18)', border: 'rgba(59,130,246,0.15)' },
    { icon: GitBranch, title: 'Hierarchical Knowledge Mapping', desc: 'Every query is contextually linked from Topic → Chapter → Subject → Course → Board, ensuring all responses are syllabus-aligned with dedicated source citations for transparency.', gradient: 'linear-gradient(135deg,#059669,#22c55e)', glow: 'rgba(16,185,129,0.18)', border: 'rgba(16,185,129,0.15)' },
    { icon: BookMarked, title: 'Subject Cards as Knowledge Hubs', desc: 'The Browser page presents each subject as an interactive card — a mini knowledge hub that acts as the structured data source powering context-aware, syllabus-aligned AI responses.', gradient: 'linear-gradient(135deg,#f97316,#fbbf24)', glow: 'rgba(245,158,11,0.18)', border: 'rgba(245,158,11,0.15)' },
    { icon: MessageSquareText, title: 'AI Chat with Source Citations', desc: 'The integrated AI chat provides accurate, personalized answers supported by clear source citations. Multi-stage retrieval keeps answers grounded — no hallucination, no off-topic responses.', gradient: 'linear-gradient(135deg,#db2777,#f43f5e)', glow: 'rgba(244,63,94,0.18)', border: 'rgba(244,63,94,0.15)' },
    { icon: Users, title: 'Who We Serve', desc: 'Students across Assam preparing for AHSEC, SEBA, Gauhati University, and Dibrugarh University exams — covering Class 11, Class 12, and undergraduate degree programmes.', gradient: 'linear-gradient(135deg,#4f46e5,#8b5cf6)', glow: 'rgba(99,102,241,0.18)', border: 'rgba(99,102,241,0.15)' },
  ],
  as: [
    { icon: Rocket, title: 'আমাৰ লক্ষ্য', desc: 'Syrabit.ai হৈছে এক AI-চালিত শৈক্ষিক মঞ্চ যি অসমৰ AHSEC, SEBA, আৰু ডিগ্ৰীৰ ছাত্ৰ-ছাত্ৰীৰ বাবে পাঠ্যক্ৰম-সামঞ্জস্যপূৰ্ণ, নিৰ্ভৰযোগ্য, আৰু প্ৰসংগ-সচেতন শিক্ষণ প্ৰদান কৰিবলৈ ডিজাইন কৰা হৈছে।', gradient: 'linear-gradient(135deg,#7c3aed,#8b5cf6)', glow: 'rgba(139,92,246,0.18)', border: 'rgba(139,92,246,0.15)' },
    { icon: Library, title: 'মঞ্চৰ আৰ্কিটেকচাৰ', desc: 'শৈক্ষিক বিষয়বস্তু গাঁথনিমূলক বিষয় কাৰ্ডত সংগঠিত কৰা হৈছে যি নিবেদিত জ্ঞান কেন্দ্ৰ হিচাপে কাম কৰে — এক পাৰস্পৰিক, যাচাইযোগ্য শিক্ষণ অভিজ্ঞতাৰ বাবে।', gradient: 'linear-gradient(135deg,#2563eb,#06b6d4)', glow: 'rgba(59,130,246,0.18)', border: 'rgba(59,130,246,0.15)' },
    { icon: GitBranch, title: 'স্তৰভিত্তিক জ্ঞান মেপিং', desc: 'প্ৰতিটো প্ৰশ্ন বিষয়বস্তু → অধ্যায় → বিষয় → পাঠ্যক্ৰম → বোৰ্ডৰ সৈতে প্ৰাসংগিকভাৱে সংযুক্ত, সকলো উত্তৰ পাঠ্যক্ৰম-সামঞ্জস্যপূৰ্ণ আৰু উৎস উদ্ধৃতিসহ নিশ্চিত কৰে।', gradient: 'linear-gradient(135deg,#059669,#22c55e)', glow: 'rgba(16,185,129,0.18)', border: 'rgba(16,185,129,0.15)' },
    { icon: BookMarked, title: 'বিষয় কাৰ্ড জ্ঞান কেন্দ্ৰ হিচাপে', desc: 'ব্ৰাউজাৰ পৃষ্ঠাত প্ৰতিটো বিষয় এটা পাৰস্পৰিক কাৰ্ড হিচাপে উপস্থাপন কৰা হয় — এক ক্ষুদ্ৰ জ্ঞান কেন্দ্ৰ যি পাঠ্যক্ৰম-সামঞ্জস্যপূৰ্ণ AI উত্তৰ চালিত কৰে।', gradient: 'linear-gradient(135deg,#f97316,#fbbf24)', glow: 'rgba(245,158,11,0.18)', border: 'rgba(245,158,11,0.15)' },
    { icon: MessageSquareText, title: 'উৎস উদ্ধৃতিসহ AI চেট', desc: 'সমন্বিত AI চেটে স্পষ্ট উৎস উদ্ধৃতিৰ সমৰ্থনত সঠিক, ব্যক্তিগত উত্তৰ প্ৰদান কৰে। বহু-স্তৰীয় পুনৰুদ্ধাৰে উত্তৰ ভিত্তিযুক্ত ৰাখে।', gradient: 'linear-gradient(135deg,#db2777,#f43f5e)', glow: 'rgba(244,63,94,0.18)', border: 'rgba(244,63,94,0.15)' },
    { icon: Users, title: 'আমি কাক সেৱা কৰোঁ', desc: 'অসমত AHSEC, SEBA, গুৱাহাটী বিশ্ববিদ্যালয়, আৰু ডিব্ৰুগড় বিশ্ববিদ্যালয়ৰ পৰীক্ষাৰ প্ৰস্তুতি লোৱা ছাত্ৰ-ছাত্ৰী — একাদশ, দ্বাদশ শ্ৰেণী, আৰু স্নাতক ডিগ্ৰী কাৰ্যক্ৰম সামৰি।', gradient: 'linear-gradient(135deg,#4f46e5,#8b5cf6)', glow: 'rgba(99,102,241,0.18)', border: 'rgba(99,102,241,0.15)' },
  ],
};

const _t = {
  en: { badge: 'THE PLATFORM', heading: 'How Syrabit.ai works under the hood', sub: 'A purpose-built architecture that maps every answer to your exact syllabus — from board to topic.' },
  as: { badge: 'মঞ্চ', heading: 'Syrabit.ai-এ কেনেকৈ কাম কৰে', sub: 'এক উদ্দেশ্য-নিৰ্মিত আৰ্কিটেকচাৰ যি প্ৰতিটো উত্তৰ আপোনাৰ সঠিক পাঠ্যক্ৰমৰ সৈতে মেপ কৰে — বোৰ্ডৰ পৰা বিষয়বস্তুলৈ।' },
};

export default function PlatformSection({ contentLang = 'en' }) {
  const t = _t[contentLang] || _t.en;
  const sections = SECTIONS[contentLang] || SECTIONS.en;

  return (
    <section id="platform" className="py-28 max-w-6xl mx-auto px-5">
      <Reveal className="text-center mb-14">
        <div
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-5"
          style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(139,92,246,0.18)' }}
        >
          <Sparkles size={14} className="text-violet-600" />
          <span className="text-xs font-semibold tracking-widest text-violet-600">
            {t.badge}
          </span>
        </div>
        <h2 className="text-foreground mb-4" style={{ fontSize: 'clamp(1.8rem,4vw,2.8rem)', fontWeight: 800, letterSpacing: '-0.02em' }}>
          {t.heading}
        </h2>
        <p className="max-w-xl mx-auto text-muted-foreground" style={{ fontSize: '1.05rem' }}>
          {t.sub}
        </p>
      </Reveal>

      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
        {sections.map((s, i) => (
          <Reveal
            key={s.title}
            delay={i * 0.08}
            className="group relative rounded-3xl p-6 cursor-default transition-all duration-300 glass-card hover:-translate-y-1.5"
          >
            <div
              className="absolute inset-0 rounded-3xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none"
              style={{ background: `radial-gradient(circle at 50% 0%,${s.glow},transparent 70%)` }}
            />
            <div
              className="w-11 h-11 rounded-2xl flex items-center justify-center mb-4 relative z-10"
              style={{ background: s.gradient, boxShadow: `0 6px 20px ${s.glow}` }}
            >
              <s.icon className="w-5 h-5 text-white" />
            </div>
            <h3 className="text-foreground mb-2 relative z-10" style={{ fontWeight: 700, fontSize: '1rem' }}>{s.title}</h3>
            <p className="text-sm leading-relaxed relative z-10 text-muted-foreground">{s.desc}</p>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
