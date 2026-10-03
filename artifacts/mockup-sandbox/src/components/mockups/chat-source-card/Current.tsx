import { BookOpen, Plus } from "lucide-react";

import "./_group.css";

export function Current() {
  return (
    <div className="source-preview">
      <header className="preview-topbar">
        <div className="preview-brand">
          <span className="preview-brand-mark"><BookOpen size={15} /></span>
          <span>Syra</span>
        </div>
        <span className="preview-topbar-label">Curriculum chat</span>
        <button className="preview-topbar-action" type="button" aria-label="New chat">
          <Plus size={15} />
        </button>
      </header>

      <main className="preview-content">
        <div className="preview-answer-meta">
          <span className="preview-answer-dot" />
          <span>Syra</span>
          <span>·</span>
          <span>Just now</span>
        </div>
        <p className="preview-answer">
          Biodiversity describes the variety of life in an ecosystem, from
          individual species to the relationships that keep habitats healthy.
        </p>

        <button className="current-source-card" type="button" aria-label="Open curriculum match">
          <div className="current-card-inner">
            <div className="current-card-meta">
              <BookOpen size={11} />
              <span>Curriculum match</span>
              <span aria-hidden="true">·</span>
              <span className="current-card-library">Syrabit Library</span>
            </div>
            <div className="current-card-path">
              <span className="current-path-badge">Topic: Biodiversity</span>
              <span className="current-path-arrow">→</span>
              <span className="current-path-badge">Chapter: Ecosystems</span>
              <span className="current-path-arrow">→</span>
              <span className="current-path-badge">Subject: Environmental Studies</span>
            </div>
            <div className="current-card-match">
              <span>High confidence</span>
              <span>92% match</span>
            </div>
            <div className="current-card-evidence">
              <strong>Method: vector search · Language: English</strong>
              “An ecosystem includes living organisms and the physical environment
              with which they interact.”
            </div>
          </div>
        </button>

        <aside className="sponsor-preview" aria-label="Sponsored content">
          <div className="sponsor-preview-label">Sponsored</div>
          <p>Learning stays free with support from our sponsors.</p>
          <div className="sponsor-ad-slot">Ad slot</div>
        </aside>
      </main>

      <footer className="preview-composer">
        <div className="preview-composer-inner">
          <span>Ask a follow-up…</span>
          <span className="preview-composer-icon"><Plus size={15} /></span>
        </div>
      </footer>
    </div>
  );
}