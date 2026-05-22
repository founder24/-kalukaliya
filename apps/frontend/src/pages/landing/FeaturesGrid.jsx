import {
  Brain, BookOpen, Layers, Clock, BarChart3, Shield,
  Sparkles, Cpu, GraduationCap, MessageSquare,
} from 'lucide-react';
import Reveal from './Reveal';

const FEATURES = {
  en: [
    { icon: Brain, title: 'AI-Powered Answers', desc: 'Browse and ask questions on any chapter — get instant, syllabus-grounded answers based on AssamBoard content, not generic internet data.', gradient: 'linear-gradient(135deg,#7c3aed,#8b5cf6)', glow: 'rgba(139,92,246,0.18)', border: 'rgba(139,92,246,0.15)' },
    { icon: BookOpen, title: 'Structured Subject Browser', desc: 'Every chapter across AssamBoard divisions (AHSEC, DEGREE, SEBA) organized by class and stream — so you always know where to start.', gradient: 'linear-gradient(135deg,#2563eb,#06b6d4)', glow: 'rgba(59,130,246,0.18)', border: 'rgba(59,130,246,0.15)' },
    { icon: Layers, title: 'Multi-format Content', desc: 'Notes, solved examples, formulas, PYQ insights, and chapter summaries — all formats exam boards love.', gradient: 'linear-gradient(135deg,#059669,#22c55e)', glow: 'rgba(16,185,129,0.18)', border: 'rgba(16,185,129,0.15)' },
    { icon: Clock, title: 'Chat History', desc: 'Every conversation auto-saved and searchable. Revisit any explanation without starting over.', gradient: 'linear-gradient(135deg,#f97316,#fbbf24)', glow: 'rgba(245,158,11,0.18)', border: 'rgba(245,158,11,0.15)' },
    { icon: BarChart3, title: 'Credit System', desc: 'Transparent usage tracking. Starter (500/day, ₹99) or Pro (4,000/day, ₹999) — credits reset daily at midnight UTC.', gradient: 'linear-gradient(135deg,#db2777,#f43f5e)', glow: 'rgba(244,63,94,0.18)', border: 'rgba(244,63,94,0.15)' },
    { icon: Shield, title: 'Secure & Private', desc: 'Your study data is encrypted, never sold, and never shared. Study without surveillance.', gradient: 'linear-gradient(135deg,#4f46e5,#8b5cf6)', glow: 'rgba(99,102,241,0.18)', border: 'rgba(99,102,241,0.15)' },
  ],
  as: [
    { icon: Brain, title: 'AI-চালিত উত্তৰ', desc: 'যিকোনো অধ্যায়ত প্ৰশ্ন ব্ৰাউজ কৰক আৰু সোধক — অসম বোৰ্ডৰ বিষয়বস্তুৰ ওপৰত ভিত্তি কৰি তাৎক্ষণিক, পাঠ্যক্ৰম-সামঞ্জস্যপূৰ্ণ উত্তৰ পাওক।', gradient: 'linear-gradient(135deg,#7c3aed,#8b5cf6)', glow: 'rgba(139,92,246,0.18)', border: 'rgba(139,92,246,0.15)' },
    { icon: BookOpen, title: 'গাঁথনিমূলক বিষয় ব্ৰাউজাৰ', desc: 'অসম বোৰ্ডৰ সকলো বিভাগৰ (AHSEC, DEGREE, SEBA) প্ৰতিটো অধ্যায় শ্ৰেণী আৰু শাখা অনুসৰি সজোৱা — যাতে আপুনি সদায় ক\'ৰ পৰা আৰম্ভ কৰিব জানে।', gradient: 'linear-gradient(135deg,#2563eb,#06b6d4)', glow: 'rgba(59,130,246,0.18)', border: 'rgba(59,130,246,0.15)' },
    { icon: Layers, title: 'বহু-ফৰ্মেট বিষয়বস্তু', desc: 'টোকা, সমাধান কৰা উদাহৰণ, সূত্ৰ, PYQ অন্তৰ্দৃষ্টি, আৰু অধ্যায়ৰ সাৰাংশ — পৰীক্ষা বোৰ্ডে পছন্দ কৰা সকলো ফৰ্মেট।', gradient: 'linear-gradient(135deg,#059669,#22c55e)', glow: 'rgba(16,185,129,0.18)', border: 'rgba(16,185,129,0.15)' },
    { icon: Clock, title: 'চেট ইতিহাস', desc: 'প্ৰতিটো কথোপকথন স্বয়ংক্ৰিয়ভাৱে সংৰক্ষিত আৰু সন্ধানযোগ্য। নতুনকৈ আৰম্ভ নকৰাকৈ যিকোনো ব্যাখ্যা পুনৰ চাওক।', gradient: 'linear-gradient(135deg,#f97316,#fbbf24)', glow: 'rgba(245,158,11,0.18)', border: 'rgba(245,158,11,0.15)' },
    { icon: BarChart3, title: 'ক্ৰেডিট ব্যৱস্থা', desc: 'স্বচ্ছ ব্যৱহাৰ ট্ৰেকিং। Starter (৫০০/দিন, ₹৯৯) বা Pro (৪,০০০/দিন, ₹৯৯৯) — ক্ৰেডিট প্ৰতিদিন মাজনিশা UTC-ত ৰিছেট হয়।', gradient: 'linear-gradient(135deg,#db2777,#f43f5e)', glow: 'rgba(244,63,94,0.18)', border: 'rgba(244,63,94,0.15)' },
    { icon: Shield, title: 'সুৰক্ষিত আৰু ব্যক্তিগত', desc: 'আপোনাৰ অধ্যয়নৰ তথ্য এনক্ৰিপ্ট কৰা হয়, কেতিয়াও বিক্ৰী কৰা নহয়, আৰু কেতিয়াও শ্বেয়াৰ কৰা নহয়।', gradient: 'linear-gradient(135deg,#4f46e5,#8b5cf6)', glow: 'rgba(99,102,241,0.18)', border: 'rgba(99,102,241,0.15)' },
  ],
};

const STEPS = {
  en: [
    { num: '01', title: 'Create your free account', desc: 'Sign up in under 30 seconds with email — no credit card needed. Get Starter (300 credits) for ₹99 or Pro (4000 credits) for ₹999.', icon: GraduationCap },
    { num: '02', title: 'Pick your subject', desc: "Browse the library by board, class, and stream. Save subjects you're preparing for and jump straight into the material.", icon: BookOpen },
    { num: '03', title: 'Ask Syra — your study companion', desc: 'Ask anything about your syllabus. Syra responds with grounded answers, worked examples, formulas, and PYQ insights — instantly.', icon: MessageSquare },
  ],
  as: [
    { num: '01', title: 'আপোনাৰ বিনামূলীয়া একাউণ্ট তৈয়াৰ কৰক', desc: 'ইমেইলেৰে ৩০ ছেকেণ্ডতকৈ কম সময়ত চাইন আপ কৰক — ক্ৰেডিট কাৰ্ডৰ প্ৰয়োজন নাই। ₹৯৯-ত Starter (৩০০ ক্ৰেডিট) বা ₹৯৯৯-ত Pro (৪০০০ ক্ৰেডিট) পাওক।', icon: GraduationCap },
    { num: '02', title: 'আপোনাৰ বিষয় বাছনি কৰক', desc: 'বোৰ্ড, শ্ৰেণী, আৰু শাখা অনুসৰি লাইব্ৰেৰী ব্ৰাউজ কৰক। আপুনি প্ৰস্তুতি লোৱা বিষয়বোৰ সংৰক্ষণ কৰক আৰু পোনে পোনে সামগ্ৰীত যাওক।', icon: BookOpen },
    { num: '03', title: 'Syra-ক সোধক — আপোনাৰ অধ্যয়ন সংগী', desc: 'আপোনাৰ পাঠ্যক্ৰমৰ বিষয়ে যিকোনো কথা সোধক। Syra-ই ভিত্তিযুক্ত উত্তৰ, সমাধান কৰা উদাহৰণ, সূত্ৰ, আৰু PYQ অন্তৰ্দৃষ্টি তাৎক্ষণিকভাৱে প্ৰদান কৰে।', icon: MessageSquare },
  ],
};

const _t = {
  en: {
    badgeFeatures: 'EVERYTHING YOU NEED',
    headingFeatures: 'Built for AssamBoard. Optimised for results.',
    subFeatures: 'Every feature is purpose-built for AHSEC, DEGREE, and SEBA students preparing for their AssamBoard exams.',
    badgeSteps: 'HOW IT WORKS',
    headingSteps: 'Up and running in 3 steps',
  },
  as: {
    badgeFeatures: 'আপোনাক যি লাগে সকলো',
    headingFeatures: 'অসম বোৰ্ডৰ বাবে নিৰ্মিত। ফলাফলৰ বাবে অনুকূলিত।',
    subFeatures: 'প্ৰতিটো সুবিধা AHSEC, DEGREE, আৰু SEBA-ৰ ছাত্ৰ-ছাত্ৰীৰ অসম বোৰ্ড পৰীক্ষাৰ প্ৰস্তুতিৰ বাবে বিশেষভাৱে নিৰ্মিত।',
    badgeSteps: 'কেনেকৈ কাম কৰে',
    headingSteps: '৩টা পদক্ষেপত আৰম্ভ কৰক',
  },
};

export default function FeaturesGrid({ contentLang = 'en' }) {
  const t = _t[contentLang] || _t.en;
  const features = FEATURES[contentLang] || FEATURES.en;
  const steps = STEPS[contentLang] || STEPS.en;

  return (
    <>
      <section id="features" className="py-28 max-w-6xl mx-auto px-5">
        <Reveal className="text-center mb-14">
          <div
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-5"
            style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(139,92,246,0.18)' }}
          >
            <Sparkles size={14} className="text-violet-600" />
            <span className="text-xs font-semibold tracking-widest text-violet-600">
              {t.badgeFeatures}
            </span>
          </div>
          <h2 className="text-foreground mb-4" style={{ fontSize: 'clamp(1.8rem,4vw,2.8rem)', fontWeight: 800, letterSpacing: '-0.02em' }}>
            {t.headingFeatures}
          </h2>
          <p className="max-w-xl mx-auto text-muted-foreground" style={{ fontSize: '1.05rem' }}>
            {t.subFeatures}
          </p>
        </Reveal>

        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
          {features.map((f, i) => (
            <Reveal
              key={f.title}
              delay={i * 0.08}
              className="group relative rounded-3xl p-6 cursor-default transition-all duration-300 glass-card hover:-translate-y-1.5"
            >
              <div
                className="absolute inset-0 rounded-3xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none"
                style={{ background: `radial-gradient(circle at 50% 0%,${f.glow},transparent 70%)` }}
              />
              <div
                className="w-11 h-11 rounded-2xl flex items-center justify-center mb-4 relative z-10"
                style={{ background: f.gradient, boxShadow: `0 6px 20px ${f.glow}` }}
              >
                <f.icon className="w-5 h-5 text-white" />
              </div>
              <h3 className="text-foreground mb-2 relative z-10" style={{ fontWeight: 700, fontSize: '1rem' }}>{f.title}</h3>
              <p className="text-sm leading-relaxed relative z-10 text-muted-foreground">{f.desc}</p>
            </Reveal>
          ))}
        </div>
      </section>

      <section id="how-it-works" className="py-28 max-w-5xl mx-auto px-5">
        <Reveal className="text-center mb-14">
          <div
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full mb-5"
            style={{ background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(139,92,246,0.18)' }}
          >
            <Cpu size={14} className="text-violet-600" />
            <span className="text-xs font-semibold tracking-widest text-violet-600">{t.badgeSteps}</span>
          </div>
          <h2 className="text-foreground" style={{ fontSize: 'clamp(1.8rem,4vw,2.8rem)', fontWeight: 800, letterSpacing: '-0.02em' }}>
            {t.headingSteps}
          </h2>
        </Reveal>

        <div className="grid sm:grid-cols-3 gap-8 relative">
          <div
            className="hidden sm:block absolute top-8 left-[20%] right-[20%] h-px pointer-events-none"
            style={{ background: 'linear-gradient(to right,transparent,rgba(139,92,246,0.20),transparent)' }}
          />

          {steps.map((step, i) => (
            <Reveal
              key={step.num}
              delay={i * 0.12}
              className="relative flex flex-col items-center text-center"
            >
              <div className="relative mb-6">
                <div
                  className="w-16 h-16 rounded-2xl flex items-center justify-center transition-transform duration-300 ease-out hover:scale-110"
                  style={{
                    background: 'linear-gradient(135deg,rgba(124,58,237,0.12),rgba(109,40,217,0.06))',
                    border: '1px solid rgba(139,92,246,0.20)',
                    boxShadow: '0 0 30px rgba(139,92,246,0.08)',
                  }}
                >
                  <step.icon className="w-7 h-7 text-violet-600" />
                </div>
                <div
                  className="absolute -top-2 -right-2 w-6 h-6 rounded-full flex items-center justify-center text-white"
                  style={{ background: 'linear-gradient(135deg,#7c3aed,#8b5cf6)', fontSize: 10, fontWeight: 800 }}
                >
                  {i + 1}
                </div>
              </div>
              <h3 className="text-foreground mb-3" style={{ fontWeight: 700, fontSize: '1.05rem' }}>{step.title}</h3>
              <p className="text-sm leading-relaxed text-muted-foreground">{step.desc}</p>
            </Reveal>
          ))}
        </div>
      </section>
    </>
  );
}
