import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import TopicTrafficTab from './TopicTrafficTab';
import { adminTopicTraffic } from '@/utils/api';

vi.mock('@/utils/api', () => ({
  adminTopicTraffic: vi.fn(),
}));

function makeReport(page = 1, totalPages = 1) {
  const eligibleTopic = {
    topic_group_id: 'topic-group',
    topic_id: 'topic-1',
    topic_title: 'Verified measures',
    subject_id: 'subject-1',
    subject_name: 'Physics',
    chapter_id: 'chapter-1',
    chapter_title: 'Evidence chapter',
    language: 'en',
    canonical_path: '/ahsec/hs-2nd-year/physics/evidence/topic/verified-measures',
    canonical_url: 'https://syrabit.ai/ahsec/hs-2nd-year/physics/evidence/topic/verified-measures',
    eligible: true,
    exclusion_reason: null,
    metrics: {
      daily: [{
        date: '2026-10-24',
        partial_day: true,
        page_view_events: 12,
        site_sessions: 12,
        organic_search_landings: 10,
        ai_referral_landings: 1,
        human_discovery_visits: 11,
        human_target_per_day: 10,
        human_target_status: 'in_progress',
        server_page_requests: null,
        verified_crawler_fetches: null,
        crawler_target_per_day: 10,
        crawler_target_status: 'not_available',
      }],
      rolling_7d: {
        page_view_events: 12,
        site_sessions: 12,
        organic_search_landings: 10,
        ai_referral_landings: 1,
        human_discovery_visits: 11,
        human_target: 70,
        human_target_status: 'not_available',
        server_page_requests: null,
        verified_crawler_fetches: null,
        crawler_target: 70,
        crawler_target_status: 'not_available',
      },
      search_console: { clicks: null, impressions: null, status: 'not_connected' },
      crawler_response_outcomes: {
        successful_html: null,
        blocked: null,
        failed: null,
        non_html: null,
        unverified_identity: null,
        status: 'not_ingested',
      },
    },
    diagnostics: [{ code: 'human_telemetry_coverage_unverified', message: 'Full-window telemetry coverage is unverified.' }],
  };
  const excludedTopic = {
    ...eligibleTopic,
    topic_group_id: 'incomplete-group',
    topic_id: 'topic-2',
    topic_title: 'Incomplete translation',
    language: 'as',
    canonical_path: null,
    canonical_url: null,
    eligible: false,
    exclusion_reason: 'assamese_translation_incomplete',
  };
  return {
    schema_version: 'topic-traffic-report-v1',
    generated_at: '2026-10-24T12:00:00.000Z',
    timezone: 'UTC',
    inventory: {
      version: 'topic-url-inventory-v1',
      candidate_url_count: 2,
      eligible_url_count: 1,
      excluded_url_count: 1,
      exclusions_by_reason: { assamese_translation_incomplete: 1 },
      pages: { page, page_size: 1, total_pages: totalPages },
    },
    targets: {
      human_discovery_visits_per_eligible_url_per_day: 10,
      verified_crawler_fetches_per_eligible_url_per_day: 10,
      rolling_7d_per_eligible_url: 70,
    },
    window: {
      daily_window_start: '2026-10-18T00:00:00.000Z',
      daily_window_end: '2026-10-24T00:00:00.000Z',
      rolling_7d_start: '2026-10-17T12:00:00.000Z',
      rolling_7d_end: '2026-10-24T12:00:00.000Z',
    },
    source_coverage: {
      first_party_analytics: {
        status: 'partial',
        coverage_start: null,
        earliest_observed_event_at: '2026-10-24T11:00:00.000Z',
        latest_event_at: '2026-10-24T12:00:00.000Z',
        rolling_7d_complete: false,
        freshness: 'recent',
        note: 'Observed consented events only; collection start is not verified.',
      },
      search_console: { status: 'not_connected', clicks: null, impressions: null, note: 'Connect Search Console.' },
      cloudflare_request_logs: { status: 'not_ingested', successful_html_fetches: null, note: 'Request logs are not ingested.' },
      tollbit: { status: 'unverified', attributed_requests: null, revenue: null, note: 'No attribution is claimed.' },
    },
    subject_language_breakdown: [{
      subject_id: 'subject-1',
      subject_name: 'Physics',
      language: 'en',
      candidate_urls: 2,
      eligible_urls: 1,
      excluded_urls: 1,
      human_discovery_visits_7d: 11,
      verified_crawler_fetches_7d: null,
    }],
    definitions: { page_view_events: 'Browser events.', verified_crawler_fetches: 'Verified final HTML responses.' },
    topics: page === 1 ? [eligibleTopic] : [excludedTopic],
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('TopicTrafficTab', () => {
  it('shows observed human proxy separately from N/A server, Search Console, and crawler measurements', async () => {
    adminTopicTraffic.mockResolvedValue({ data: makeReport() });
    render(<TopicTrafficTab adminToken="admin-token" />);

    expect(await screen.findByTestId('topic-traffic-inventory')).toBeTruthy();
    expect(adminTopicTraffic).toHaveBeenCalledWith('admin-token', 1, 100);
    expect(screen.getByTestId('topic-traffic-candidate-count').textContent).toContain('2');
    expect(screen.getByTestId('topic-traffic-eligible-count').textContent).toContain('1');
    expect(screen.getByTestId('topic-traffic-source-search-console').textContent).toContain('not connected');
    expect(screen.getByTestId('topic-traffic-source-cloudflare').textContent).toContain('not ingested');
    expect(screen.getByTestId('topic-traffic-source-tollbit').textContent).toContain('unverified');
    expect(screen.getByTestId('topic-traffic-topic-row-topic-group-en-0')).toBeTruthy();
    expect(screen.getByTestId('topic-traffic-canonical-url-topic-group-en-0').getAttribute('href'))
      .toBe('https://syrabit.ai/ahsec/hs-2nd-year/physics/evidence/topic/verified-measures');

    fireEvent.click(screen.getByTestId('topic-traffic-expand-topic-group-en-0'));
    const details = await screen.findByTestId('topic-traffic-topic-details-topic-group-en-0');
    expect(details.textContent).toContain('not available');
    expect(details.textContent).toContain('N/A');
    expect(details.textContent).toContain('Search Console');
  });

  it('loads the next inventory page without losing the report context', async () => {
    adminTopicTraffic.mockImplementation(async (_token, page) => ({ data: makeReport(page, 2) }));
    render(<TopicTrafficTab adminToken="admin-token" />);

    expect(await screen.findByTestId('topic-traffic-topic-row-topic-group-en-0')).toBeTruthy();
    fireEvent.click(screen.getByTestId('topic-traffic-page-next'));

    await waitFor(() => expect(screen.getByTestId('topic-traffic-page-label').textContent).toBe('2 / 2'));
    expect(screen.getByTestId('topic-traffic-topic-row-incomplete-group-as-0')).toBeTruthy();
    expect(adminTopicTraffic).toHaveBeenLastCalledWith('admin-token', 2, 100);
    expect(screen.getByTestId('topic-traffic-exclusion-assamese_translation_incomplete').textContent).toContain('1');
  });
});

