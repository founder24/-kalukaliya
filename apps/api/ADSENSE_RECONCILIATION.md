# AdSense reconciliation and release configuration

## Production configuration verified

On 2026-10-25, the live Cloudflare settings for `syrabit-api-prod` had no
`ADSENSE_REPORT_URL` binding and no `ADSENSE_REPORT_TOKEN` secret binding. The
daily scheduled reconciliation therefore records a `skipped` run with the
failure summary “AdSense report endpoint is not configured.” The admin ad
revenue screen reports the endpoint and last-run status without displaying a
private URL or credential.

The required `ADSENSE_REPORT_URL` is a private aggregate-report endpoint. It
must accept a requested referral-week period and return exactly one finalized
INR report in the contract validated by `apps/api/src/services/referral-roi.ts`.
If authentication is required, configure `ADSENSE_REPORT_TOKEN` as a Worker
secret. Do not add the token to `wrangler.toml`, frontend variables, or build
logs. After provisioning the endpoint and token, trigger the daily Worker
reconciliation and confirm a completed run with no failures in the Referral
ROI or Ad Revenue admin screens.

## Production manual slot inventory verified

The Cloudflare Pages production project `syrabitfrontend` had numeric values for
all 20 active manual-slot keys, but only 13 distinct IDs; eight placements
shared one ID. The project also had one obsolete `VITE_ADS_ADSENSE_CHAPTER_QA_TOP_SLOT`
key that is not used by the active placement map. Reused IDs prevent reliable
placement attribution.

The release environment loader now rejects missing, malformed, or duplicate
active IDs before the production build. Create distinct AdSense units for the
active placements that require attribution, update the corresponding Pages
environment values, and verify the release log names all 20 validated keys.
Do not use fabricated numeric values to satisfy the release check.

## Evidence boundaries

- `ad_revenue_reports` contains finalized aggregate provider receipts only.
- Browser impression events and manually entered values are not provider
  revenue and are not accepted by the admin revenue report.
- The current provider endpoint contract returns one aggregate report per
  referral week. It does not provide slot-level revenue; distinct units make
  provider-side placement reporting possible, but this Worker does not claim
  per-slot earnings.
- AdSense Auto Ads and manual placements remain gated by production build,
  completed auth state, and the account's ad opt-out preference. The current
  visitor choice is opt-out (not a separate explicit ads-consent prompt); the
  optional analytics cookie choice is separate. Chapter pages pause Auto Ads
  and use manual slots; Learn and standalone PYQ pages allow Auto Ads. Unit and
  release browser tests cover denied/withdrawn ads consent, manual slot counts,
  distinct IDs per page, and fluid no-fill collapse.
