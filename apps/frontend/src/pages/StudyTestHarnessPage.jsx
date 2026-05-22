/**
 * StudyTestHarnessPage — minimal scaffold for Task #594 Playwright tests.
 *
 * The HighlightSavePopover + QuizModal Phase-3 study surfaces only
 * appear inside the heavy `ChapterPage` reader (which itself depends on
 * a tall stack of content APIs, library bundles, analytics, and SSR
 * preload data). Mocking the entire chapter render in an e2e test is
 * brittle — a content-API field renaming would silently break the test.
 *
 * Instead, this page mounts the same study components inside a
 * deterministic `data-savable` block with stable selectors. The route
 * is excluded from the public sitemap and SEO indexes; it exists so
 * the e2e suite can exercise the highlight → save → quiz flow without
 * coupling to chapter-content shape.
 *
 * It is also handy as a manual repro aid when iterating on the
 * popover / quiz UX.
 */
import { HighlightSavePopover } from '@/components/study/HighlightSavePopover';
import { QuizModal } from '@/components/study/QuizModal';
import { useState } from 'react';

const _SAMPLE = `Photosynthesis is the process by which green plants and some other organisms use sunlight to synthesize foods with the help of chlorophyll. The general equation is 6CO2 + 6H2O → C6H12O6 + 6O2. The process occurs primarily in the chloroplasts of plant cells.`;

export default function StudyTestHarnessPage() {
  const [quizOpen, setQuizOpen] = useState(false);
  return (
    <div data-testid="study-harness" style={{ padding: 24, maxWidth: 720, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 12 }}>
        Study harness (test fixture)
      </h1>
      <p style={{ fontSize: 13, color: '#666', marginBottom: 16 }}>
        Used by the e2e suite to drive the highlight / save / quiz flow.
      </p>
      <div
        data-savable="true"
        data-testid="harness-savable"
        style={{
          padding: 16,
          border: '1px solid #ddd',
          borderRadius: 12,
          lineHeight: 1.6,
        }}
      >
        {_SAMPLE}
      </div>
      <div style={{ marginTop: 16 }}>
        <button
          data-testid="harness-open-quiz"
          onClick={() => setQuizOpen(true)}
          style={{
            padding: '8px 14px',
            borderRadius: 10,
            background: '#7c3aed',
            color: 'white',
            fontWeight: 600,
            border: 0,
            cursor: 'pointer',
          }}
        >
          Open Quiz
        </button>
      </div>
      <HighlightSavePopover
        sourceUrl="https://example.org/sample-chapter"
        sourceTitle="Photosynthesis — Test Harness"
        chapterRef="test/harness/photosynthesis"
        subjectName="Biology"
      />
      <QuizModal
        open={quizOpen}
        onClose={() => setQuizOpen(false)}
        topic="Photosynthesis"
        subject_name="Biology"
        chapter_ref="test/harness/photosynthesis"
        count={3}
      />
    </div>
  );
}
