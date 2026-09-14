# Recurring Influencer Referral Program Policy

**Policy version:** 2026-09-14  
**Status:** Defined, not yet open  
**Production authority:** Cloudflare API Worker and D1 only

This policy supersedes earlier Campus Lead and Adcash/Adsterra planning documents. Those documents are historical proposals, not program terms or evidence that referral rewards are funded.

## 1. Admission and the 100-slot cohort

- The program has exactly 100 approved influencer slots. Approval is not first-click enrollment.
- An applicant must complete identity and payout-recipient verification, any required KYC, an academic profile, and a program-integrity review. The academic profile must show a genuine connection to a student, educator, institution, or relevant academic community.
- One person or controlled organization may occupy only one slot. Duplicate, synthetic, purchased, or misrepresented identities are ineligible.
- During initial cohort selection, eligible completed applicants receive an authoritative admission-priority decision under the published eligibility and integrity criteria. After all 100 slots are filled, later eligible applicants enter a timestamped waitlist. An incomplete or failed application does not reserve a place.
- Four consecutive open program weeks with zero mature verified visitors makes an influencer inactive. Paused weeks do not count. After notice, the influencer has seven calendar days to respond before the slot can be released.
- Fraud signals, identity/KYC failure, prohibited promotion, misleading reward claims, or material terms violations may cause immediate suspension. Suspension stops new accrual at the authoritative D1 timestamp but does not erase previously approved obligations.
- A suspended influencer keeps occupying the slot until removal is final after the appeal deadline or decision. A replacement cannot be approved early, so the cohort cannot exceed 100.
- A suspended or removed influencer may appeal with evidence within 14 calendar days. Accrual remains stopped during review.
- A vacant basic slot is claimed atomically in D1 and offered to the next eligible, fully reviewed applicant by waitlist completion timestamp. A replacement always starts in the basic tier.

## 2. Weekly period and mature verified visitors

- A week runs from Monday 00:00:00 through the next Monday 00:00:00 in `Asia/Kolkata`.
- A visitor counts only when the Worker/D1 verification process marks the campaign identity mature, unique, eligible, and free of invalid-traffic signals.
- The same campaign identity can count at most once across all influencers in one week. The first valid D1 claim wins; later or duplicate claims pay no influencer.
- A campaign identity may count again in a later week only after fresh qualifying activity. That is a retention/activity reward, not a new-customer acquisition claim.
- Client beacons, page views, clicks, ad opportunities, filled impressions, and projected revenue do not by themselves create a reward.

## 3. Basic reward curve

Every approved influencer begins in the basic tier.

| Mature verified weekly visitors | Basic reward |
| ---: | ---: |
| 0 | ₹0 |
| 1–100 | ₹1 per visitor |
| 101–499 | ₹100 total |
| 500 or more in a qualifying week | ₹100 total |

Rewards reset each week. They do not accumulate toward a permanent unlock.

## 4. Advanced qualification and reward curve

- At most 30 influencers can hold advanced positions concurrently. The first 30 ordered members of the approved cohort to reach 500 mature verified visitors within one week receive initial provisional-review priority.
- Ordering is the authoritative D1 timestamp at which the 500th visitor matures. If timestamps are identical, the stable influencer identifier sorts ascending. D1 claims that ordering atomically.
- Reaching 500 creates provisional qualification only. The qualifying week remains basic-capped at ₹100.
- The first 30 ordered qualifiers enter provisional review. Later qualifying influencers remain in an ordered qualified waitlist for future vacancies.
- Fraud and quality review must approve the provisional qualification. Advanced earning starts at the first weekly boundary after approval, never midweek or retroactively. If review misses the immediately following boundary, activation waits for the next one.
- An advanced influencer earns ₹1 per mature verified weekly visitor, capped at ₹1,000.
- The other 70 approved influencers remain basic-capped at ₹100.
- A vacated advanced position is not inherited by a replacement influencer. D1 atomically reserves each available position before moving the corresponding ordered candidate from the qualified waitlist into provisional review; active plus reserved positions can never exceed 30. If approved, activation occurs at the next weekly boundary.
- If an appeal reverses an advanced removal before replacement is final, the position is restored. After replacement, the successful appellant receives priority for the next vacancy; no retroactive advanced earnings are created.

At full occupancy, maximum weekly reward exposure is:

`30 × ₹1,000 + 70 × ₹100 = ₹37,000`

₹37,000 is the required worst-case weekly reserve, not a one-time campaign budget and not a prediction of ad revenue.

## 5. Funding and week-opening gate

A new week fails closed unless all of the following are true:

1. The program is active.
2. A segregated, finite reward reserve has at least ₹37,000 unencumbered after all approved and unpaid obligations.
3. Finalized net-revenue evidence from the approved production network covers a provider period ending no more than seven days before the opening decision.
4. Finalized invalid-traffic and quality evidence is available and no more than 24 hours old.
5. Attribution, deduplication, fraud review, and settlement controls are healthy.

Google AdSense is the only currently approved production ad network. Adcash, Adsterra, AdPushup, PropellerAds, or another network contributes no funding evidence unless separately approved through brand-safety and policy review.

The program does not guarantee that advertising revenue will exceed rewards. Finalized provider reports, including deductions and invalid-traffic adjustments, are the evidence used for continuation decisions. Projected opportunities, client analytics, or gross estimates cannot authorize a week or a settlement.

## 6. Pause, resume, and permanent closure

- An authorized operator with `referral:policy` capability may pause the program manually.
- Automatic pause triggers include reserve shortfall, missing or stale revenue/quality data, approved-network suspension, fraud anomalies, or unavailable attribution/verification controls.
- D1 records the authoritative pause timestamp. Accrual occurs at maturity, not click or claim time: only a visitor that matured before the pause remains reward-eligible. Earlier activity that had not matured may still be reviewed for fraud and analytics, but it creates no reward if maturity occurs at or after the pause. Previously approved obligations remain payable.
- Referral links may remain usable as ordinary navigation while paused, but the UI and terms must not promise that visits are earning rewards.
- Resume requires a recorded authorized decision plus the same reserve, evidence, quality, and control-health gates required to open a week. Resume creates a new authoritative timestamp and does not backfill the paused interval.
- Permanent closure is irreversible for that policy version. It stops new accrual while preserving verification, appeals, records, and valid approved obligations.

## 7. Verification, adjustment, and settlement

- Dashboard amounts are estimates until the weekly fraud, deduplication, identity, and provider-quality reviews finish.
- Settlement may be delayed while evidence is incomplete, disputed, or subject to provider invalid-traffic adjustments.
- Duplicate, automated, incentivized, self-referred, manipulated, or otherwise invalid traffic can be removed before approval with a reason code and appeal path.
- A pause cannot retroactively remove valid accrued rewards. Approved obligations remain due; only a documented correction of duplicate or invalid traffic may adjust an amount.
- Automatic UPI payout, tax treatment, and legal advice are outside this policy.

## 8. Production and access boundary

- Referral attribution, lifecycle, reserves, review, and settlement must be implemented only in the Cloudflare API Worker with D1 as the system of record.
- The retired Python backend must not expose referral or payout routes or become a second control plane.
- Policy mutations require `referral:policy`; fraud/identity decisions require `referral:review`; settlement approval requires `referral:settle`. Generic staff access is insufficient.
- Cookie-authenticated mutations require CSRF validation and an approved origin. All mutations require audit records, actor identity, reason, policy version, and authoritative timestamps.
- No runtime referral or payout endpoint is active until the downstream ledger, onboarding, settlement, and ROI-validation work is completed and approved.