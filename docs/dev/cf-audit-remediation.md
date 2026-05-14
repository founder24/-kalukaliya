# Cloudflare Audit — Manual Remediation Steps

Two items in the weekly Cloudflare audit (`cloudflare-full-audit.js`) require manual
operator actions in the CF dashboard.  Both are one-time tasks; once done, the next
Monday audit automatically confirms the fix with no code changes.

---

## 1. Configure Zaraz GA4 (item #19 — currently FAIL)

**Why:** Error 7003 (`"No route for that URI"`) means the Zaraz configuration does not
exist for the `syrabit.ai` zone.  Client-side `window.gtag()` calls rely on Zaraz
server-side forwarding; without it, no GA4 analytics events are sent.

**Prerequisites:**
1. Your `CLOUDFLARE_API_TOKEN` must have the **Zaraz: Edit** permission scope.
2. You need the GA4 Measurement ID (`G-XXXXXXXXXX`) from
   `analytics.google.com → Admin → Data Streams → your web stream`.
3. `node` ≥ 18 must be available in the shell.

**Command:**

```bash
cd artifacts/syrabit
GA4_MEASUREMENT_ID=G-XXXXXXXXXX \
  CLOUDFLARE_API_TOKEN=<token-with-zaraz-edit> \
  CLOUDFLARE_ZONE_ID=5b8c97df4431491dc7f60ea72fb61871 \
  node scripts/cloudflare-phase6-apply.js
```

The script runs Step 3 (Zaraz GA4 tool creation) automatically when
`GA4_MEASUREMENT_ID` is set.

**Verify:** Wait for the next Monday audit digest, or run the audit script directly:

```bash
CLOUDFLARE_API_TOKEN=<read-token> \
  CLOUDFLARE_ZONE_ID=5b8c97df4431491dc7f60ea72fb61871 \
  CLOUDFLARE_ACCOUNT_ID=<account-id> \
  node artifacts/syrabit/scripts/cloudflare-full-audit.js
```

Item #19 should show `✓ PASS` once the Zaraz GA4 tool is active and enabled.

---

## 2. Delete legacy mTLS certificate `syrabit-railway-mtls` (item #17 — currently WARN)

**Why:** The Railway backend was decommissioned in Task #335.  The mTLS client
certificate that was used to authenticate requests to Railway still exists in the
Cloudflare account.  It is not actively used, but its presence is flagged as a WARN
until deleted.  Once deleted, the audit automatically flips to PASS.

**Steps:**

1. Go to **[Cloudflare dashboard → SSL/TLS → Client Certificates]**:
   ```
   https://dash.cloudflare.com/<ACCOUNT_ID>/ssl-tls/client-certificates
   ```
2. Search for a certificate named **`syrabit-railway-mtls`**.
3. Click the **⋮ (three-dot menu)** → **Revoke** (then confirm).  
   Some accounts show a **Delete** option instead of Revoke — use whichever is available.
4. Confirm deletion in the dialog.

**Verify:** The next Monday audit will call
`GET /accounts/{id}/mtls_certificates` and confirm the certificate is absent.
Item #17 will flip from `⚠ WARN` to `✓ PASS` automatically.

**Note:** If your API token lacks `SSL and Certificates: Read` scope, item #17 shows
`⚠ WARN (token lacks SSL and Certificates: Read scope)` instead.  Add the scope to
the token or verify deletion manually in the dashboard.

---

## Reference

| Audit item | Status before fix | Status after fix |
|:----------:|:-----------------:|:----------------:|
| #17 mTLS cert | ⚠️ WARN | ✅ PASS (cert absent) or ⚠️ WARN (scope gap) |
| #19 Zaraz GA4 | ❌ FAIL | ✅ PASS |

Audit script: `artifacts/syrabit/scripts/cloudflare-full-audit.js`  
Phase 6 apply script: `artifacts/syrabit/scripts/cloudflare-phase6-apply.js`
