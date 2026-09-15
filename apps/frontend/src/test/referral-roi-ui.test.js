import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const root = path.resolve(import.meta.dirname, '..');
const page = fs.readFileSync(path.join(root, 'pages/referrals/ReferralROI.jsx'), 'utf8');
const api = fs.readFileSync(path.join(root, 'utils/api.jsx'), 'utf8');

describe('referral ROI evidence export contracts', () => {
  it('offers download only from the capability-gated ROI panel', () => {
    expect(page).toContain("canStaffCapability(user, 'referral:settle')");
    expect(page).toContain('adminExportReferralRoi(adminToken)');
    expect(page).toContain('button-download-referral-roi');
    expect(page).toContain('referral-roi-evidence.json');
    expect(page).toContain('Download evidence');
  });

  it('requests the read-only dashboard export as a JSON attachment', () => {
    expect(api).toContain('/admin/referrals/roi/dashboard/export');
    expect(api).toContain("responseType: 'blob'");
  });
});