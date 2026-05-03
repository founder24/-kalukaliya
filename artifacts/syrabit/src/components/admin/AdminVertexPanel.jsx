import { useState } from 'react';
import {
  Search, Languages, BarChart2, Lightbulb, TrendingUp, FileSearch,
  Eye, Brain, CreditCard, ListChecks, Network,
} from 'lucide-react';
import StatusHeader from './vertex-panel/StatusHeader';
import ProviderRoutingCard from './vertex-panel/ProviderRoutingCard';
import SemanticSearchCard from './vertex-panel/SemanticSearchCard';
import TranslationCard from './vertex-panel/TranslationCard';
import QualityScoreCard from './vertex-panel/QualityScoreCard';
import TopicSuggesterCard from './vertex-panel/TopicSuggesterCard';
import SeoMetaCard from './vertex-panel/SeoMetaCard';
import ContentGapsCard from './vertex-panel/ContentGapsCard';
import VisionOcrCard from './vertex-panel/VisionOcrCard';
import NlpConceptsCard from './vertex-panel/NlpConceptsCard';
import FlashcardGeneratorCard from './vertex-panel/FlashcardGeneratorCard';
import McqGeneratorCard from './vertex-panel/McqGeneratorCard';
import AdminQuickLinks from './AdminQuickLinks';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const SERVICE_CARDS = [
  { id: 'routing',    label: 'Provider Routing',    icon: Network,     color: '#8b5cf6',  component: ProviderRoutingCard },
  { id: 'semantic',   label: 'Semantic Search',    icon: Search,      color: '#3b82f6',  component: SemanticSearchCard },
  { id: 'translate',  label: 'Translation',         icon: Languages,   color: '#10b981',  component: TranslationCard },
  { id: 'quality',    label: 'Quality Scorer',      icon: BarChart2,   color: '#f59e0b',  component: QualityScoreCard },
  { id: 'topics',     label: 'Topic Suggester',     icon: Lightbulb,   color: '#a855f7',  component: TopicSuggesterCard },
  { id: 'seo',        label: 'SEO Meta Generator',  icon: TrendingUp,  color: '#06b6d4',  component: SeoMetaCard },
  { id: 'gaps',       label: 'Content Gaps',        icon: FileSearch,  color: '#ef4444',  component: ContentGapsCard },
  { id: 'ocr',        label: 'Vision OCR',          icon: Eye,         color: '#f97316',  component: VisionOcrCard },
  { id: 'nlp',        label: 'NLP Concepts',        icon: Brain,       color: '#a855f7',  component: NlpConceptsCard },
  { id: 'flashcards', label: 'Flashcard Generator', icon: CreditCard,  color: '#06b6d4',  component: FlashcardGeneratorCard },
  { id: 'mcq',        label: 'MCQ Generator',       icon: ListChecks,  color: '#10b981',  component: McqGeneratorCard },
];

export default function AdminVertexPanel({ token, adminToken, onNavigate }) {
  const tk = adminToken || token;
  const [active, setActive] = useState('routing');

  const ActiveCard = SERVICE_CARDS.find(s => s.id === active)?.component;

  return (
    <SectionErrorBoundary name="AI Studio">
      <div style={{ padding: '0 2px' }}>
        <StatusHeader token={tk} />

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 6, marginBottom: 24 }}>
          {SERVICE_CARDS.map(s => {
            const Icon = s.icon;
            const isActive = active === s.id;
            return (
              <button key={s.id} onClick={() => setActive(s.id)}
                style={{
                  background: isActive ? `${s.color}14` : '#ffffff',
                  border: `1px solid ${isActive ? s.color + '44' : '#e5e7eb'}`,
                  borderRadius: 12, padding: '10px 14px', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8, transition: 'all 0.15s',
                  textAlign: 'left', boxShadow: isActive ? 'none' : '0 1px 3px rgba(0,0,0,0.04)',
                }}>
                <Icon size={15} color={isActive ? s.color : '#9ca3af'} />
                <span style={{ fontSize: 12, fontWeight: 700, color: isActive ? s.color : '#6b7280' }}>
                  {s.label}
                </span>
              </button>
            );
          })}
        </div>

        {ActiveCard && <ActiveCard token={tk} onNavigate={onNavigate} />}

        <div style={{ marginTop: 24, padding: 16, background: '#f5f3ff', border: '1px solid #e9d5ff', borderRadius: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: '#7c3aed', marginBottom: 8, textTransform: 'uppercase' }}>Also Available In Other Panels</div>
          <div style={{ fontSize: 12, color: '#6b7280', lineHeight: 1.8 }}>
            • <strong style={{ color: '#111827' }}>CMS Editor</strong> — Translate button on any document (Google Translate / Gemini / Workers AI fallback)<br />
            • <strong style={{ color: '#111827' }}>Content Studio</strong> — Enhance + Quality Score on generated blocks (Gemini → Cloudflare AI fallback)<br />
            • <strong style={{ color: '#111827' }}>Thumbnail Studio</strong> — Image analysis (Gemini Vision / Cloudflare AI)<br />
            • <strong style={{ color: '#111827' }}>Document Upload</strong> — Extract topics/MCQs from AHSEC PDFs (Gemini / Azure Document Intelligence)<br />
            • <strong style={{ color: '#111827' }}>Vision OCR</strong> — Scan question paper images (Google Cloud Vision / Bedrock)<br />
            • <strong style={{ color: '#111827' }}>NLP Concepts</strong> — Entity &amp; keyword extraction (Cloud Natural Language / Cohere)<br />
            • <strong style={{ color: '#111827' }}>Flashcard + MCQ</strong> — Generate student revision material from any chapter (Gemini / Workers AI)
          </div>
        </div>
        <AdminQuickLinks links={['seomanager','content','analytics','dashboard']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
