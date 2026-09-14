import { describe, expect, it } from 'vitest';
import { canStaffCapability, isStaffOrAdmin } from './staffAccess';

describe('staff access rules', () => {
  it('allows both staff and admin users into the full staff portal', () => {
    expect(isStaffOrAdmin({ role: 'staff' })).toBe(true);
    expect(isStaffOrAdmin({ role: 'admin' })).toBe(true);
  });

  it('does not grant staff portal access to ordinary users', () => {
    expect(isStaffOrAdmin({ role: 'student' })).toBe(false);
    expect(isStaffOrAdmin(null)).toBe(false);
  });

  it('preserves capability-scoped controls for explicitly limited staff', () => {
    expect(canStaffCapability({ role: 'staff', capabilities: ['content:edit'] }, 'content:edit')).toBe(true);
    expect(canStaffCapability({ role: 'staff', capabilities: ['content:edit'] }, 'content:delete')).toBe(false);
    expect(canStaffCapability({ role: 'staff', capabilities: null }, 'content:delete')).toBe(true);
    expect(canStaffCapability({ role: 'student', capabilities: null }, 'content:edit')).toBe(false);
  });
});