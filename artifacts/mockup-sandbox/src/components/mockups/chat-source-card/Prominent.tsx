import { ArrowUpRight, BookOpen, Plus } from "lucide-react";

import "./_group.css";

export function Prominent() {
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

        <button className="prominent-source-card" type="button" aria-label="Open Environmental Studies curriculum source">
          <div className="prominent-card-inner">
            <div className="prominent-card-heading">
              <span className="prominent-card-icon"><BookOpen size={17} /></span>
              <span>
                <span className="prominent-card-source">
                  Curriculum source <span aria-hidden="true">·</span>
                  <span className="source-kind">Syrabit Library</span>
                </span>
                <span className="prominent-card-title">Environmental Studies</span>
              </span>
              <ArrowUpRight size={15} color="#92b9a3" />
            </div>
            <div className="prominent-card-chips">
              <span className="prominent-card-chip"><strong>Topic</strong> Biodiversity</span>
              <span className="prominent-card-chip"><strong>Chapter</strong> Ecosystems</span>
              <span className="prominent-card-chip"><strong>Class</strong> Degree</span>
              <span className="prominent-card-chip"><strong>Course</strong> VAC</span>
            </div>
            <div className="prominent-card-evidence">
              <div className="prominent-card-evidence-meta">
                <span>High confidence</span>
                <span>92% match</span>
                <span>English source</span>
              </div>
              “An ecosystem includes living organisms and the physical environment
              with which they interact.”
            </div>
          </div>
        </button>
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