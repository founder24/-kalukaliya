import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const root = path.resolve(import.meta.dirname, '..');
const student = fs.readFileSync(
  path.join(root, 'pages/referrals/ReferralExperience.jsx'),
  'utf8',
);
const staff = fs.readFileSync(
  path.join(root, 'pages/referrals/ReferralAdmissions.jsx'),
  'utf8',
);
const app = fs.readFileSync(path.join(root, 'App.jsx'), 'utf8');

describe('referral onboarding browser contracts', () => {
  it('renders server-authoritative earnings and never derives a payout', () => {
    expect(student).toContain('currentWeek?.authoritative_reward_inr');
    expect(student).toContain('Server-verified earnings');
    expect(student).not.toMatch(/mature_verified\s*[*]\s*\d/);
    expect(student).not.toMatch(/reward_eligible\s*[*]\s*\d/);
  });

  it('does not collect payment details during admission', () => {
    expect(student).toContain('We do not collect UPI or beneficiary details.');
    expect(student).not.toMatch(/name=["'](?:upi|bank|account_number|ifsc)/i);
  });

  it('only renders the sharing kit from an activated server payload', () => {
    expect(student).toContain('{dashboard?.referral &&');
    expect(student).toContain('QRCodeSVG value={dashboard.referral.link}');
    expect(student).toContain("application.status === 'activation_required'");
  });

  it('keeps the student route authenticated and staff review capability-gated', () => {
    expect(app).toContain(
      '<Route path="/profile/referrals" element={<AuthGuard><ReferralPage /></AuthGuard>} />',
    );
    expect(staff).toContain("canStaffCapability(user, 'referral:review')");
    expect(staff).toContain('identity_verified: identity');
    expect(staff).toContain('kyc_verified: kyc');
  });
});