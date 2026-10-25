import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import StaffManualChapterForm from './StaffManualChapterForm';

function renderForm(editView) {
  return render(
    <StaffManualChapterForm
      editView={editView}
      editTarget={editView === 'edit-chapter' ? { id: 'chapter-1', status: 'draft' } : null}
      contentForm={{ title: 'Unit I', topics: ['Cells', 'Mitosis'] }}
      setContentForm={vi.fn()}
      subjectData={{ name: 'Biology' }}
      saving={false}
      onSave={vi.fn()}
      onCancel={vi.fn()}
      adminToken="test-token"
    />,
  );
}

describe('StaffManualChapterForm topic-card contract', () => {
  it('offers initial topic titles when creating a chapter', () => {
    renderForm('new-chapter');

    expect(screen.getByTestId('staff-chapter-topics')).toHaveValue('Cells, Mitosis');
    expect(screen.getByText(/saved as draft topic cards/i)).toBeTruthy();
  });

  it('keeps existing topic cards read-only in chapter edits', () => {
    renderForm('edit-chapter');

    expect(screen.queryByTestId('staff-chapter-topics')).toBeNull();
    expect(screen.getByTestId('staff-chapter-topic-guidance')).toHaveTextContent(
      'This chapter has 2 topic cards.',
    );
    expect(screen.getByText(/Manage question answer cards/i)).toBeTruthy();
  });
});
