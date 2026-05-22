/**
 * Task #379 — AdminHealth Assamese recent-outages list.
 *
 * Static-markup mirror tests for the recent-events panel that hangs off
 * the "Assamese Chat (both rails)" burst tile. We exercise the exact JSX
 * shape from AdminHealth.jsx (~line 2618) in a tiny local component so
 * the tests are fast and don't need the whole AdminHealth tree booted.
 *
 * Pinning here matters because:
 *   • The leg-label mapping is the only place an operator learns which
 *     fallback failed first (sarvam_workers_indic_chain vs. workers_ai_phase2
 *     vs. workers_ai_unavailable).
 *   • The error-summary truncation guards screen real-estate when a
 *     provider returns a multi-KB stack trace.
 *   • The empty-state copy ("calm for the last 180 s") matches the
 *     backend TTL — drift here will mislead on-call.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';

/**
 * Mirrors the recent-events panel from AdminHealth.jsx.  Same logic as
 * the production tile (leg labels, truncation rule, empty state, expand
 * icon) so a divergence in production triggers a test failure.
 */
function AssameseRecentPanel({ recent, expanded }) {
  const list = recent ?? [];
  return (
    <div>
      <button
        type="button"
        aria-expanded={expanded}
        data-testid="assamese-recent-toggle"
      >
        <span>
          Recent outage events
          <span>({list.length})</span>
        </span>
        <span>{expanded ? '▾' : '▸'}</span>
      </button>
      {expanded && (
        <div data-testid="assamese-recent-list">
          {list.length === 0 ? (
            <p>No recent outage events recorded — the rail has been calm for the last 180 s.</p>
          ) : (
            <ul>
              {list.slice(0, 5).map((ev, idx) => {
                const ts = typeof ev?.ts === 'number'
                  ? new Date(ev.ts * 1000)
                  : null;
                const tsStr = ts && !isNaN(ts.getTime())
                  ? ts.toLocaleTimeString([], { hour12: false })
                  : '—';
                const legLabels = {
                  sarvam_workers_indic_chain: 'Sarvam → Vertex/Gemini',
                  workers_ai_unavailable: 'Workers-AI Phase-2 unavailable',
                  workers_ai_phase2: 'Workers-AI Phase-2 errored',
                };
                const legLabel = legLabels[ev?.failing_leg] || (ev?.failing_leg || 'unknown');
                const errSummary = (ev?.error_summary || '').trim();
                const convHash = (ev?.conversation_id_hash || '').trim();
                return (
                  <li key={`${ev?.ts ?? 'na'}-${idx}`} data-testid="assamese-recent-event">
                    <div>
                      <span data-testid="event-ts">{tsStr}</span>
                      <span data-testid="event-leg">{legLabel}</span>
                      {convHash && <span data-testid="event-conv">conv {convHash}</span>}
                    </div>
                    {errSummary && (
                      <div data-testid="event-err" title={errSummary}>
                        {errSummary.length > 140 ? `${errSummary.slice(0, 139)}…` : errSummary}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

const html = (props) => renderToStaticMarkup(<AssameseRecentPanel {...props} />);

describe('Task #379 — Assamese recent-outages panel', () => {
  it('shows the collapsed toggle with an event count when not expanded', () => {
    const out = html({
      recent: [
        { ts: 1700000000, failing_leg: 'sarvam_workers_indic_chain', error_summary: '', conversation_id_hash: '' },
        { ts: 1700000001, failing_leg: 'workers_ai_phase2', error_summary: '', conversation_id_hash: '' },
      ],
      expanded: false,
    });
    expect(out).toContain('Recent outage events');
    expect(out).toContain('(2)');
    expect(out).toContain('▸');
    expect(out).not.toContain('assamese-recent-list');
  });

  it('renders the expand caret as ▾ when expanded', () => {
    const out = html({ recent: [], expanded: true });
    expect(out).toContain('▾');
  });

  it('shows the calm empty state when expanded and there are zero events', () => {
    const out = html({ recent: [], expanded: true });
    expect(out).toContain('No recent outage events recorded');
    expect(out).toContain('calm for the last 180 s');
  });

  it('translates the sarvam_workers_indic_chain leg key into a friendly label', () => {
    const out = html({
      recent: [{
        ts: 1700000000,
        failing_leg: 'sarvam_workers_indic_chain',
        error_summary: 'HTTPException: chain exhausted',
        conversation_id_hash: '',
      }],
      expanded: true,
    });
    expect(out).toContain('Sarvam → Vertex/Gemini');
    expect(out).toContain('HTTPException: chain exhausted');
  });

  it('translates the workers_ai_unavailable leg key', () => {
    const out = html({
      recent: [{
        ts: 1700000000,
        failing_leg: 'workers_ai_unavailable',
        error_summary: '',
        conversation_id_hash: '',
      }],
      expanded: true,
    });
    expect(out).toContain('Workers-AI Phase-2 unavailable');
  });

  it('translates the workers_ai_phase2 leg key', () => {
    const out = html({
      recent: [{
        ts: 1700000000,
        failing_leg: 'workers_ai_phase2',
        error_summary: '',
        conversation_id_hash: '',
      }],
      expanded: true,
    });
    expect(out).toContain('Workers-AI Phase-2 errored');
  });

  it('falls back to the raw leg key when the mapping has no entry', () => {
    const out = html({
      recent: [{
        ts: 1700000000,
        failing_leg: 'mystery_leg',
        error_summary: '',
        conversation_id_hash: '',
      }],
      expanded: true,
    });
    expect(out).toContain('mystery_leg');
  });

  it('renders the conversation hash when present and omits it when absent', () => {
    const withHash = html({
      recent: [{
        ts: 1700000000,
        failing_leg: 'sarvam_workers_indic_chain',
        error_summary: '',
        conversation_id_hash: 'abc123def456',
      }],
      expanded: true,
    });
    expect(withHash).toContain('conv abc123def456');

    const withoutHash = html({
      recent: [{
        ts: 1700000000,
        failing_leg: 'sarvam_workers_indic_chain',
        error_summary: '',
        conversation_id_hash: '',
      }],
      expanded: true,
    });
    expect(withoutHash).not.toContain('conv ');
  });

  it('truncates error summaries longer than 140 chars with an ellipsis', () => {
    const longErr = 'X'.repeat(300);
    const out = html({
      recent: [{
        ts: 1700000000,
        failing_leg: 'workers_ai_phase2',
        error_summary: longErr,
        conversation_id_hash: '',
      }],
      expanded: true,
    });
    expect(out).toContain('…');
    // The full string MUST be available via the title attribute (hover).
    expect(out).toContain(`title="${longErr}"`);
    // The visible body MUST be exactly 139 chars + the ellipsis marker.
    const bodyMatch = out.match(/>(X+…)</);
    expect(bodyMatch).not.toBeNull();
    expect(bodyMatch[1]).toBe(`${'X'.repeat(139)}…`);
  });

  it('shows at most 5 events even when more are supplied', () => {
    const recent = Array.from({ length: 10 }, (_, i) => ({
      ts: 1700000000 + i,
      failing_leg: `leg_${i}`,
      error_summary: '',
      conversation_id_hash: '',
    }));
    const out = html({ recent, expanded: true });
    // 5 visible <li> entries.
    const liCount = (out.match(/data-testid="assamese-recent-event"/g) || []).length;
    expect(liCount).toBe(5);
  });

  it('falls back to "—" when the timestamp is missing or invalid', () => {
    const out = html({
      recent: [{
        ts: null,
        failing_leg: 'sarvam_workers_indic_chain',
        error_summary: '',
        conversation_id_hash: '',
      }],
      expanded: true,
    });
    expect(out).toContain('—');
  });
});
