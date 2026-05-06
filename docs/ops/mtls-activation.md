# mTLS Activation Operational Checklist

**Purpose:** Fully activate the mTLS "fail-closed" gate between the Cloudflare
edge worker (`syrabit-edge`) and the Azure Container Apps backend
(Task #336 — replaces the legacy Railway origin). Complete all four steps
in order — skipping or misordering them will either break production
traffic or leave the gate unarmed.

> **Origin migration note (Task #336).** Every reference to "Railway"
> below means the same operation on the Azure Container Apps
> service `syrabit-backend`. Concretely, replace any
> `railway variables set FOO=bar` with
> `az containerapp update -n syrabit-backend -g <rg> --set-env-vars FOO=bar`, and
> any Railway dashboard click-path with the equivalent `az containerapp`
> command (or Azure Portal under Container Apps →
> syrabit-backend → Settings → App-Level Environment Variables).
> The TLS-level "require client certificate" toggle on the Railway
> Networking tab maps to **App Platform → Settings → Domains →
> Custom Domain → Edit → Client Certificate "Require"** on the
> `api.syrabit.ai` custom domain.

**Scope:** Manual operational steps only. Code automation is covered by
`inject-mtls-cert-id.js` (CI gate) and the `MtlsClientCertMiddleware` in the
backend (already deployed).

---

## Four required gates (in order)

| Gate | What it does |
|---|---|
| **1. Issue cert** | Cloudflare generates the mTLS keypair; you capture the UUID and private key |
| **2. Store secrets** | Private key, fingerprint, and UUID reach Wrangler, GitHub CI, and Railway |
| **3. Railway requires cert** | TLS-level enforcement — connections without the cert are dropped at the network layer |
| **4. Arm fail-closed gate** | Worker returns 503 if its cert binding is ever absent; backend rejects unsigned requests |

> Completing all four gates makes the bypass attack surface zero-knowledge — a leaked
> `BACKEND_ORIGIN_SECRET` alone cannot reach the backend.

---

## Prerequisites

| Item | Where to get it |
|---|---|
| `CLOUDFLARE_API_TOKEN` with **SSL and Certificates: Edit** scope | dash.cloudflare.com → My Profile → API Tokens |
| Wrangler CLI authenticated (`wrangler whoami`) | `pnpm dlx wrangler login` |
| Access to GitHub repo secrets (Settings → Secrets → Actions) | Repo admin |
| Railway project access (deploy environment) | Railway dashboard |

---

## Step 1 — Issue the mTLS client certificate and capture the UUID

Run the Phase 6 apply script. It calls the Cloudflare mTLS certificate API,
generates a Cloudflare-managed keypair (10-year lifetime), and prints the
certificate UUID and private key.

```bash
CLOUDFLARE_API_TOKEN=<your-token> \
  node artifacts/syrabit/scripts/cloudflare-phase6-apply.js
```

**Expected output (abridged):**

```
── Step 1: Issue mTLS client certificate ──
  ✓  mTLS certificate issued: id=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx expires=2035-...
  ✓  SHA-256 fingerprint:     <64-char-hex>

  ══════════════════════════════════════════════════════════════
  SAVE THE PRIVATE KEY — it is shown only once:
  ══════════════════════════════════════════════════════════════
  -----BEGIN PRIVATE KEY-----
  ...
  -----END PRIVATE KEY-----
  ══════════════════════════════════════════════════════════════
```

**Important:** the private key is shown exactly once. Copy it to a secure
location (1Password / Bitwarden vault) before the terminal session ends.

If a `syrabit-railway-mtls` certificate already exists, the script prints the
existing `id` and `expires_on` without re-issuing. Use that UUID for the steps
below.

### Verification

```bash
CLOUDFLARE_API_TOKEN=<your-token> \
  node -e "(async () => {
    const r = await fetch(
      'https://api.cloudflare.com/client/v4/accounts/d66e40eac539fff1db270fddf384a5ec/mtls_certificates',
      { headers: { Authorization: 'Bearer ' + process.env.CLOUDFLARE_API_TOKEN } }
    );
    const j = await r.json();
    console.log(j.result.map(c => c.name + '  ' + c.id + '  ' + c.expires_on));
  })()"
```

Expected: `syrabit-railway-mtls  <uuid>  <expiry-date>` appears in the output.

---

## Step 2 — Store the private key and fingerprint as Wrangler secrets

The edge worker needs the private key to present the certificate during the TLS
handshake, and the fingerprint is used by `MtlsClientCertMiddleware` on the
Railway backend to validate the connection.

**2a. Store the private key:**

```bash
echo "-----BEGIN PRIVATE KEY-----
<paste-private-key-PEM-here>
-----END PRIVATE KEY-----" | wrangler secret put MTLS_PRIVATE_KEY --name syrabit-edge
```

Expected output:
```
✨ Success! Uploaded secret MTLS_PRIVATE_KEY
```

**2b. Store the certificate SHA-256 fingerprint (64-char hex, no colons):**

If the apply script printed the fingerprint, use it directly:

```bash
echo "<64-char-hex-fingerprint>" | wrangler secret put MTLS_CERT_SHA256 --name syrabit-edge
```

If the fingerprint was not printed (edge case — cert PEM not returned by list
API), compute it from the saved PEM:

```bash
openssl x509 -fingerprint -sha256 -noout -in cert.pem \
  | sed 's/.*=//;s/://g' | tr A-F a-f \
  | wrangler secret put MTLS_CERT_SHA256 --name syrabit-edge
```

**2c. Add the certificate UUID to GitHub Actions secrets** (required for CI
injection on every deploy — see `inject-mtls-cert-id.js`):

```
GitHub → Settings → Secrets and variables → Actions → New repository secret
  Name:  CF_MTLS_CERT_ID
  Value: <uuid-from-step-1>
```

**2d. Store the same fingerprint on Railway** (read by `MtlsClientCertMiddleware`):

```
Railway dashboard → syrabit-backend service → Variables → Add
  CF_MTLS_CERT_SHA256 = <64-char-hex-fingerprint>
```

### Verification

```bash
wrangler secret list --name syrabit-edge
```

Expected: both `MTLS_PRIVATE_KEY` and `MTLS_CERT_SHA256` appear in the list.

Check Railway: the `CF_MTLS_CERT_SHA256` variable appears in the service
variables list (Railway dashboard → service → Variables tab).

---

## Step 3 — Configure Railway to require the client certificate at the TLS layer

This is the gold-standard protection: Railway's TLS stack rejects connections
that do not present the Cloudflare mTLS certificate before HTTP is reached.
Any request that arrives without the cert (e.g., using a leaked
`BACKEND_ORIGIN_SECRET`) is dropped at the network layer.

**Do this step AFTER Step 2 and AFTER the next `wrangler deploy` completes.**
Enabling Railway mTLS before the worker is deployed with the cert binding would
block all traffic including legitimate worker requests.

**Deploy the worker first** (so the cert binding is live):

```bash
cd workers/edge-proxy
CF_MTLS_CERT_ID=<uuid-from-step-1> node scripts/inject-mtls-cert-id.js
wrangler deploy
```

Expected output from `inject-mtls-cert-id.js`:
```
[inject-mtls] wrangler.toml updated: certificate_id = "<uuid>"
[inject-mtls] [[mtls_certificates]] binding is now active for this deploy.
```

Expected from `wrangler deploy`:
```
✨  Built successfully
🌎  Deployed syrabit-edge ... routes: api.syrabit.ai/*, syrabit.ai/*, www.syrabit.ai/*
```

**Now configure Railway mTLS:**

```
Railway dashboard → syrabit-backend service → Settings → Networking → mTLS
  → Enable mTLS
  → Upload the certificate PEM (the public certificate, not the private key)
  → Set "Required" (reject connections without the cert)
  → Save
```

The certificate PEM to upload is the `certificate` field returned by the
Cloudflare API (the `BEGIN CERTIFICATE` block). If you no longer have it,
retrieve it from the Cloudflare dashboard:

```
dash.cloudflare.com → SSL/TLS → Client Certificates → syrabit-railway-mtls
  → Download certificate
```

### Verification

Run a direct probe against the Railway backend **without** the client cert.
The connection must be rejected at the TLS layer (connection error, not HTTP
403):

```bash
curl -sv --max-time 10 \
  https://workspacemockup-sandbox-production-df37.up.railway.app/api/health \
  2>&1 | grep -E 'SSL|certificate|curl: \(|HTTP/'
```

Expected: `curl: (35)` or `SSL certificate problem` or `Connection reset` —
NOT `HTTP/2 200`. Any HTTP response means TLS-level enforcement is not yet
active.

Also confirm the nightly smoke probe passes (assertion `6a-iv`):

```bash
CLOUDFLARE_API_TOKEN=<tok> \
  node artifacts/syrabit/scripts/nightly-smoke.js 2>&1 | grep -i mtls
```

Expected: `✓  6a-iv  bypass: direct Railway hit rejected at TLS layer`.

---

## Step 4 — Arm the fail-closed gate (MTLS_REQUIRED=true)

With Railway requiring the cert at TLS level, the final step is to arm the
edge worker's own fail-closed guard. When `MTLS_REQUIRED=true`, the worker's
`proxyToBackend()` returns **503** instead of falling back to plain `fetch` if
the `MTLS_CERT` binding is absent. This prevents any future deploy accident
(e.g., a wrangler.toml misconfiguration) from silently bypassing mTLS.

**Do this step last — only after Step 3 is verified working.**

```bash
echo -n "true" | wrangler secret put MTLS_REQUIRED --name syrabit-edge
```

Expected output:
```
✨ Success! Uploaded secret MTLS_REQUIRED
```

Enable application-layer enforcement on the Railway backend:

```
Railway dashboard → syrabit-backend service → Variables → Add (or update)
  ENFORCE_MTLS = true
```

The `MtlsClientCertMiddleware` will now reject requests that are missing the
`X-Cf-Mtls-Active` HMAC proof header (injected by the edge worker on every
proxied request, signed with `BACKEND_ORIGIN_SECRET`).

### Verification

Confirm the worker honours the gate by checking the health endpoint from the
Cloudflare edge (this goes through the worker with the cert):

```bash
curl -s https://api.syrabit.ai/api/health | python3 -m json.tool
```

Expected: `{"status": "ok", "service": "Syrabit.ai API", ...}` — HTTP 200.

Confirm a direct-to-Railway hit is now blocked at both layers:

```bash
# Should be rejected at TLS layer (no HTTP response):
curl -sv --max-time 10 \
  https://workspacemockup-sandbox-production-df37.up.railway.app/api/health \
  2>&1 | grep -E 'curl: \(|HTTP/'
```

Expected: `curl: (35)` or similar TLS error — no HTTP response.

Run the full nightly smoke to confirm all Phase 6 assertions pass:

```bash
CLOUDFLARE_API_TOKEN=<tok> \
  node artifacts/syrabit/scripts/nightly-smoke.js
```

Expected: all `6a-*` assertions show `✓`. No `FAIL` lines in the mTLS section.

---

## Summary checklist

| # | Step | Command / Action | Done |
|---|---|---|---|
| 1 | Issue cert, save UUID + private key | `node cloudflare-phase6-apply.js` | `[ ]` |
| 2a | Store private key as Wrangler secret | `wrangler secret put MTLS_PRIVATE_KEY` | `[ ]` |
| 2b | Store cert fingerprint as Wrangler secret | `wrangler secret put MTLS_CERT_SHA256` | `[ ]` |
| 2c | Add UUID to GitHub Actions secrets | `CF_MTLS_CERT_ID = <uuid>` in repo secrets | `[ ]` |
| 2d | Set fingerprint on Railway | `CF_MTLS_CERT_SHA256` Railway variable | `[ ]` |
| 3a | Deploy worker with cert binding | `inject-mtls-cert-id.js && wrangler deploy` | `[ ]` |
| 3b | Configure Railway TLS-level mTLS | Railway → Settings → mTLS → Required | `[ ]` |
| 3c | Verify direct Railway hit is TLS-rejected | `curl -sv ...railway.app/api/health` | `[ ]` |
| 4a | Arm worker fail-closed gate | `wrangler secret put MTLS_REQUIRED` (value: `true`) | `[ ]` |
| 4b | Enable app-layer enforcement on Railway | `ENFORCE_MTLS=true` Railway variable | `[ ]` |
| 4c | Verify Cloudflare-proxied traffic still works | `curl https://api.syrabit.ai/api/health` | `[ ]` |
| 4d | Run nightly smoke — all 6a-* assertions pass | `node nightly-smoke.js` | `[ ]` |

---

## Rollback

If mTLS enforcement breaks production traffic:

1. **Immediately:** disable the Railway TLS-level mTLS requirement
   (Railway → Service → Settings → Networking → mTLS → Disable).
2. Disarm the worker gate:
   ```bash
   echo -n "false" | wrangler secret put MTLS_REQUIRED --name syrabit-edge
   ```
3. Investigate using the troubleshooting steps above before re-enabling.

---

*Relevant files:*
- `workers/edge-proxy/wrangler.toml` — `[[mtls_certificates]]` binding
- `workers/edge-proxy/scripts/inject-mtls-cert-id.js` — CI injection gate
- `artifacts/syrabit/scripts/cloudflare-phase6-apply.js` — cert issuance script
- `artifacts/syrabit/scripts/nightly-smoke.js` — assertions `6a-i` through `6a-iv`
