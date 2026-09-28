import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import React from 'react';

const mocks = vi.hoisted(() => ({
  getSubjectsByCourseType: vi.fn(),
  patchProfile: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  getSubjectsByCourseType: (...args) => mocks.getSubjectsByCourseType(...args),
  apiClient: () => ({ patch: (...args) => mocks.patchProfile(...args) }),
}));

vi.mock('sonner', () => ({
  toast: { success: mocks.toastSuccess, error: mocks.toastError },
}));

vi.mock('@/components/Logo', () => ({ LogoMark: () => <span /> }));
vi.mock('./shared', () => ({
  StarRating: () => null,
  UsageDots: () => null,
}));
vi.mock('./AcademicCascadeSelector', () => ({ default: () => null }));

import AcademicDetails from './AcademicDetails';

const courseTypes = [
  {
    slug: 'major',
    name: 'Major',
    icon: 'target',
    description: 'Primary subjects',
    subject_count: 1,
    subjects: [{ id: 'math', name: 'Mathematics' }],
  },
  {
    slug: 'minor',
    name: 'Minor',
    icon: 'book',
    description: 'Additional subjects',
    subject_count: 1,
    subjects: [{ id: 'economics', name: 'Economics' }],
  },
];

function renderAcademicDetails() {
  return render(
    <AcademicDetails
      profile={{ board_id: 'degree-board', course_type: null, selected_subjects: [] }}
      isDegreeProfile
      openEdit={vi.fn()}
      onProfileUpdate={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.patchProfile.mockResolvedValue({ data: {} });
});

describe('AcademicDetails course-type selector', () => {
  it('shows a load error and allows retrying the course-type request', async () => {
    mocks.getSubjectsByCourseType
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockResolvedValueOnce({ data: courseTypes });
    renderAcademicDetails();

    fireEvent.click(screen.getByTestId('edit-field-course_type'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load course types');
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByTestId('course-type-major')).toBeInTheDocument();
    expect(mocks.getSubjectsByCourseType).toHaveBeenCalledTimes(2);
  });

  it('shows a clear empty state when the board has no course types', async () => {
    mocks.getSubjectsByCourseType.mockResolvedValueOnce({ data: [] });
    renderAcademicDetails();

    fireEvent.click(screen.getByTestId('edit-field-course_type'));

    expect(await screen.findByText('No course types are available for this board yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Course Preferences' })).toBeDisabled();
  });

  it('saves only subjects that belong to the selected course type', async () => {
    mocks.getSubjectsByCourseType.mockResolvedValueOnce({ data: courseTypes });
    renderAcademicDetails();

    fireEvent.click(screen.getByTestId('edit-field-course_type'));
    fireEvent.click(await screen.findByTestId('course-type-major'));
    fireEvent.click(screen.getByTestId('course-subject-math'));
    fireEvent.click(screen.getByTestId('course-type-minor'));
    fireEvent.click(screen.getByTestId('course-subject-economics'));
    fireEvent.click(screen.getByRole('button', { name: 'Save Course Preferences' }));

    await waitFor(() => expect(mocks.patchProfile).toHaveBeenCalledWith('/user/profile', {
      course_type: 'minor',
      stream_name: 'Minor',
      selected_subjects: [{ id: 'economics', name: 'Economics' }],
    }));
  });
});