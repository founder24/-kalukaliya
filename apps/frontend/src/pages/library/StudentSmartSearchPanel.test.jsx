import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import StudentSmartSearchPanel from './StudentSmartSearchPanel';

const api = vi.hoisted(() => ({
  getStudentSearchStatus: vi.fn(),
  searchStudentContent: vi.fn(),
}));

vi.mock('@/utils/api', () => api);

beforeEach(() => vi.stubEnv('VITE_STUDENT_SEARCH_UI_ENABLED', 'true'));
afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

function renderPanel(props = {}) {
  return render(
    <MemoryRouter>
      <StudentSmartSearchPanel {...props} />
    </MemoryRouter>,
  );
}

describe('StudentSmartSearchPanel', () => {
  it('does not call the API when the client feature flag is off', async () => {
    vi.stubEnv('VITE_STUDENT_SEARCH_UI_ENABLED', 'false');
    renderPanel();
    expect(api.getStudentSearchStatus).not.toHaveBeenCalled();
    expect(screen.queryByTestId('student-smart-search-panel')).toBeNull();
  });

  it('stays hidden until the API explicitly enables it', async () => {
    api.getStudentSearchStatus.mockResolvedValue({ enabled: false });
    renderPanel();
    await waitFor(() => expect(api.getStudentSearchStatus).toHaveBeenCalledOnce());
    expect(screen.queryByTestId('student-smart-search-panel')).toBeNull();
  });

  it('submits the selected language and scope, then renders safe source links', async () => {
    api.getStudentSearchStatus.mockResolvedValue({ enabled: true });
    api.searchStudentContent.mockResolvedValue({
      results: [{
        chapter_id: 'chapter-1',
        title: 'গতি',
        snippet: 'ত্বৰণে বেগ সলনি কৰে।',
        url: '/as/ahsec/class-12/physics/motion',
        score: 0.9,
      }],
    });

    renderPanel({ language: 'as', scope: { class_slug: 'class-12' } });
    const toggle = await screen.findByTestId('button-toggle-student-smart-search');
    fireEvent.click(toggle);
    fireEvent.change(screen.getByTestId('input-student-smart-search'), {
      target: { value: 'ত্বৰণ কেনেকৈ কাম কৰে' },
    });
    fireEvent.click(screen.getByTestId('button-submit-student-smart-search'));

    await waitFor(() => expect(api.searchStudentContent).toHaveBeenCalledWith({
      query: 'ত্বৰণ কেনেকৈ কাম কৰে',
      language: 'as',
      class_slug: 'class-12',
    }));
    expect(await screen.findByTestId('link-student-search-chapter-1')).toHaveAttribute(
      'href',
      '/as/ahsec/class-12/physics/motion',
    );
    expect(screen.getByText('ত্বৰণে বেগ সলনি কৰে।')).toBeInTheDocument();
  });
});
