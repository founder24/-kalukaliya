# Infra Dependency Audit — 2026-05-10

> **Scope:** Terraform providers (23 `.tf` files in `artifacts/syrabit/infra/aws/`),
> GitHub Actions across 31 workflow files in `.github/workflows/`,
> and 3 Cloudflare Workers (`workers/edge-proxy`, `workers/email-worker`,
> `artifacts/syrabit/workers/embed-worker`).
> Read-only audit per Task #56. **No `versions.tf`, `wrangler.toml`,
> worker `package.json`, or workflow `uses:` line was modified.**

---

## 1. Headline risks

| # | Surface | Finding | Severity | Recommendation |
|---|---|---|---|---|
| 1 | **GitHub Actions** | **8 `uses:` references are still float-tag-pinned** (4× `actions/checkout@v4`, 4× `actions/setup-python@v5`) instead of SHA-pinned. These bypass the `pinned-actions-check.yml` gate because the gate runs only on the `Replit-agent` branch (per its `on:` trigger), not on `main`. | **HIGH** — the entire `tj-actions/changed-files` 2025 incident class. Any compromise of the upstream `v4` / `v5` tag would give an attacker secret-exfil on the next push. | **dedicated PR** to SHA-pin all 8 occurrences. **Plus**, expand the gate's `on:` trigger to include `main` so this can't regress silently. |
| 2 | **GitHub Actions gate scope** | `pinned-actions-check.yml` only fires on `pull_request: branches: [Replit-agent]` and `push: branches: [Replit-agent]`. The default branch is `main`. The gate is effectively non-enforcing for the actual production branch. | **HIGH** | Add `main` to both the `pull_request: branches` and `push: branches` lists. Currently the only thing protecting `main` from a float-tagged `uses:` re-introduction is reviewer discipline, which has already failed (see #1). |
| 3 | **Workers — Wrangler** | `artifacts/syrabit/workers/embed-worker/package.json` pins `wrangler: ^3.78.0`. Wrangler 3.x reached end-of-feature in late 2025; the production edge + email workers in `workers/*` are already on `^4.0.0`. Embed worker is a major behind. | **MEDIUM** | Bump embed-worker to `wrangler ^4` in a focused PR with a `wrangler deploy --dry-run` + worker smoke (the embed worker is on the hot path for every Indic embed call so a regression hits Pinecone fan-out). |
| 4 | **Workers — compatibility_date** | The 3 wrangler.toml files under `artifacts/syrabit/workers/` (the legacy mirror tree) all carry `compatibility_date = "2024-09-23"` — **~20 months stale**. The production trees under `workers/edge-proxy` and `workers/email-worker` are on `2026-05-01`. | **MEDIUM** (only impacts the legacy/mirror tree, but ambiguity is itself a risk) | Either delete the legacy `artifacts/syrabit/workers/edge-proxy/` and `artifacts/syrabit/workers/email-worker/` trees (they look like leftovers from the move to top-level `workers/`) or bump their compat date in lock-step. Embed worker is **only** under `artifacts/syrabit/workers/` — it must be bumped (not deleted). |
| 5 | **Workers — `@cloudflare/workers-types`** | Three different floors across the three workers: `^4.20240909.0` (embed), `^4.20241205.0` (email), `^4.20260424.1` (edge-proxy). The first two are pre-2026; the latter is post-2026 D1 + DO RPC type updates. | **LOW** | Consolidate to one floor (the most-recent one) when the wrangler 4 bump for embed-worker lands. |
| 6 | **Terraform** | Only one provider declared: `hashicorp/aws >= 5.0`. The lock file resolves to `6.44.0`. **No Cloudflare provider, no Azure provider** is declared — even though the codebase deploys to all three clouds. Cloudflare resources are managed via `wrangler` directly and Azure via `bicep` + the deploy workflow, so this is **intentional**, not a gap. | **N/A — informational** | No action. The 4-cloud delegation matrix says the same. Document this in the next infra README pass so it's clear TF only owns AWS. |
| 7 | **Terraform — version constraint floor** | `_root.tf` says `version = ">= 5.0"` but the lock pins to `6.44.0`. `>= 5.0` lets a fresh `terraform init` accept anything from 5.0.0 (which is pre-IPv6-VPC and pre-modern S3 bucket-API split) up to whatever `terraform init -upgrade` resolves at the time. The lock file pins the actual install, but a green-field clone could land on a much-older AWS provider before the lock file is rebuilt. | **LOW** | Tighten to `version = "~> 6.44"` (i.e. `>= 6.44, < 7.0`) so the floor matches the actually-tested version. The lock file already enforces this in CI; the source-of-truth pin should match. |

---

## 2. Terraform inventory

### 2a. Provider versions

| Provider | Source | Source-pin | Lock-resolved | Latest (registry) | Verdict |
|---|---|---|---|---|---|
| AWS | `hashicorp/aws` | `>= 5.0` | `6.44.0` | `6.x` (latest series) | **CURRENT** — lock-file is on the active major, but source pin is one major too low (see headline #7) |
| Cloudflare | (not declared) | n/a | n/a | n/a | **N/A** — managed via `wrangler` directly |
| Azure | (not declared) | n/a | n/a | n/a | **N/A** — managed via `bicep` + GitHub Actions deploy |

### 2b. TF source files inventoried

23 `.tf` files under `artifacts/syrabit/infra/aws/`. `infra/azure/` contains
no `.tf` (only `aca-syrabit-backend.bicep`). All `provider` and
`required_providers` blocks live in `_root.tf` — no per-module overrides
were found.

### 2c. Lock file

`artifacts/syrabit/infra/aws/.terraform.lock.hcl` exists, contains the
single-provider entry above with full hash set (16 hashes — current lock
format). No drift between `required_providers` constraint and the lock.

---

## 3. GitHub Actions inventory

31 workflow files. **Total `uses:` references after de-duplication: 18
distinct action references; 105 total occurrences.**

### 3a. Float-tag-pinned (POLICY VIOLATION)

| Action | Pin | Occurrences | Risk |
|---|---|---|---|
| `actions/checkout` | `@v4` | **4** | Tag re-pointing → arbitrary code in CI w/ `GITHUB_TOKEN` write |
| `actions/setup-python` | `@v5` | **4** | Same |

**Total: 8 violations.** The `pinned-actions-check.yml` gate is the
control that should have caught these but it does not run on `main` (see
headline #2). Per task scope these are **flagged, not fixed**.

### 3b. SHA-pinned (compliant)

| Action | SHA | Tag comment | Latest | Status |
|---|---|---|---|---|
| `actions/checkout` | `de0fac2e4500dabe0009e67214ff5f5447ce83dd` | `v6.0.2` | `v6.0.2` | ✅ current |
| `actions/setup-node` | `48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e` | `v6.4.0` | `v6.4.0` | ✅ current |
| `actions/setup-python` | `a309ff8b426b58ec0e2a45f0f869d46889d02405` | `v6.2.0` | `v6.2.0` | ✅ current (note: 2 other SHAs for older v5.6.0 + v6.0.0 still in tree — see §3c) |
| `actions/setup-python` | `a26af69be951a213d495a4c3e4e4022e16d87065` | `v5.6.0` | `v6.2.0` | **stale** — bump to v6.2.0 SHA |
| `actions/setup-python` | `e9aba2c848f5ebd159c070c61ea2c4e2b122355e` | `v6.0.0` | `v6.2.0` | **stale** — bump to v6.2.0 SHA |
| `actions/upload-artifact` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | `v7.0.1` | `v7.0.1` | ✅ current |
| `actions/upload-artifact` | `65c4c4a1ddee5b72f698fdd19549f0f0fb45cf08` | `v4.6.0` | `v7.0.1` | **stale** — 3 majors behind in 1 file |
| `actions/cache` | `5a3ec84eff668545956fd18022155c47e93e2684` | `v4.2.3` | `v4.2.3` | ✅ current |
| `actions/github-script` | `d746ffe35508b1917358783b479e04febd2b8f71` | `v9.0.0` | `v9.0.0` | ✅ current |
| `pnpm/action-setup` | `fc06bc1257f339d1d5d8b3a19a8cae5388b55320` | `v5.0.0` | `v5.0.0` | ✅ current |
| `aws-actions/configure-aws-credentials` | `e3dd6a429d7300a6a4c196c26e071d42e0343502` | `v4.0.2` | `v4.x` | ✅ current |
| `aws-actions/amazon-ecr-login` | `062b18b96a7aff071d4dc91bc00c4c1a7945b076` | `v2.0.1` | `v2.x` | ✅ current |
| `azure/login` | `a457da9ea143d694b1b9c7c869ebb04ebe844ef5` | `v2.3.0` | `v2.x` | ✅ current |
| `digitalocean/action-doctl` | `135ac0aa0eed4437d547c6f12c364d3006b42824` | `v2.5.1` | `v2.x` | ✅ current |
| `docker/build-push-action` | `263435318d21b8e681c14492fe198d362a7d2c83` | `v6.18.0` | `v6.x` | ✅ current |
| `docker/setup-buildx-action` | `e468171a9de216ec08956ac3ada2f0791b6bd435` | `v3.11.1` | `v3.x` | ✅ current |
| `docker/setup-qemu-action` | `29109295f81e9208d7d86ff1c6c12d2833863392` | `v3.6.0` | `v3.x` | ✅ current |

### 3c. Stale SHA pins

3 actions are SHA-pinned (compliant with the gate) but the SHA points
to an older release than what's available:

| Action | Current SHA / tag | Latest | Verdict |
|---|---|---|---|
| `actions/setup-python` | `v5.6.0` (a26af69b…) | `v6.2.0` | bump in next dependabot pass |
| `actions/setup-python` | `v6.0.0` (e9aba2c8…) | `v6.2.0` | bump in next dependabot pass |
| `actions/upload-artifact` | `v4.6.0` (65c4c4a1…) | `v7.0.1` | **3 majors behind** — focused PR; v5+ changed artifact retention default + v7 changed compression default |

Dependabot config (`.github/dependabot.yml` per the gate's docstring) is
expected to flip both the SHA and the trailing `# vX.Y.Z` comment in
lock-step. If these stale pins are still here, dependabot is either
disabled or rate-limited on this repo — verify in a follow-up.

### 3d. Deprecated / EOL actions

None of the SHA-pinned actions in §3b have been deprecated by their
publisher as of 2026-05-10. All of `actions/{checkout, setup-node,
setup-python, upload-artifact, cache, github-script}` are on the
currently-maintained major.

---

## 4. Cloudflare Workers inventory

### 4a. Production workers (`workers/`)

| Worker | wrangler.toml `compatibility_date` | wrangler floor | `@cloudflare/workers-types` floor | Verdict |
|---|---|---|---|---|
| `workers/edge-proxy` | `2026-05-01` | `^4.0.0` | `^4.20260424.1` | ✅ current |
| `workers/email-worker` | `2026-05-01` | `^4.0.0` | `^4.20241205.0` | wrangler ✅; types floor 5 months stale (LOW) |

### 4b. Mirror / legacy workers (`artifacts/syrabit/workers/`)

| Worker | `compatibility_date` | wrangler floor | types floor | Verdict |
|---|---|---|---|---|
| `artifacts/syrabit/workers/edge-proxy` | `2024-09-23` | (not in package.json — N/A, the production is at `workers/edge-proxy`) | n/a | **STALE** — likely a leftover after the move to top-level `workers/`. Verify and **delete** if dead. |
| `artifacts/syrabit/workers/email-worker` | `2024-09-23` | n/a | n/a | **STALE** — same as above. |
| `artifacts/syrabit/workers/embed-worker` | `2024-09-23` | `^3.78.0` | `^4.20240909.0` | **MEDIUM** — embed worker is **production-active** (only copy of the embed worker). Bump compat date to `2026-05-01`, wrangler to `^4.0.0`, and types floor to `^4.20260424.1` in a focused PR with `wrangler deploy --dry-run` + worker smoke. |

### 4c. wrangler vulnerabilities

`wrangler ^3.78.0` (embed-worker) — wrangler 3.x is EOL for new bug
fixes; no critical CVEs published as of audit date but the longer the
gap the higher the chance of an `npm audit` flag landing without a
back-port. **Action:** part of the embed-worker bump in headline #3.

`wrangler ^4.0.0` (edge-proxy + email-worker) — current major. No open
advisories.

### 4d. Worker source dependencies

Production worker `package.json` files declare only **devDependencies**
(`wrangler`, `typescript`, `@cloudflare/workers-types`, plus `vite` +
`vitest` on edge-proxy for the test harness). **No runtime deps** ship to
the worker — wrangler bundles `src/index.ts` from source. The runtime
attack surface is therefore the wrangler bundler + the Workers runtime
itself, not npm packages. This is the desired posture.

---

## 5. Cross-reference: deprecated, EOL, or vulnerable infra deps

| Surface | Item | Status | Action |
|---|---|---|---|
| GitHub Actions | `actions/checkout@v4` (4 spots) | Float-tag — policy violation | **HIGH** dedicated PR |
| GitHub Actions | `actions/setup-python@v5` (4 spots) | Float-tag — policy violation | **HIGH** dedicated PR |
| GitHub Actions | `actions/upload-artifact@v4.6.0` (1 spot) | 3 majors behind | focused PR |
| GitHub Actions | `pinned-actions-check.yml` `on:` trigger | **does not include `main`** | **HIGH** — extend trigger so the gate enforces on the default branch |
| Workers | `wrangler ^3.78.0` (embed-worker) | major behind, 3.x in deprecation window | focused PR |
| Workers | 3 wrangler.toml's at `compatibility_date=2024-09-23` | 20 months stale (mirror tree) | bump or delete |
| TF | `hashicorp/aws >= 5.0` | source-pin one major below lock-file | tighten to `~> 6.44` |
| Workers | `@cloudflare/workers-types` 3-way version skew | 9 months / 5 months / current | consolidate floor |

---

## 6. Methodology notes

- TF provider scan: `rg -l 'required_providers|^\s*provider\s+"'` over
  `artifacts/syrabit/infra/aws/` returned only `_root.tf`. Cross-checked
  against the lock file in the same directory. No `provider "cloudflare"`
  or `provider "azurerm"` blocks exist anywhere in-repo.
- Actions scan: `rg --no-filename --no-line-number 'uses:\s*[a-zA-Z]' .github/workflows/`
  on the 31 `.yml` files (no `.yaml` files present). Counts in §3 are
  the raw `sort | uniq -c` output minus 2 false-positives (the comment
  lines in `pinned-actions-check.yml` itself that reference `uses:`
  inside its own help text).
- Worker scan: read each `wrangler.toml` for `compatibility_date` /
  `main` / `name`, then each `package.json` for `devDependencies`.
- Latest-version look-ups for actions were resolved against the GitHub
  releases pages of each action repo as of the audit date; no automated
  registry call was made. Treat the "Latest" column in §3b as a
  point-in-time snapshot, not a live feed.
- TF latest-version look-ups for `hashicorp/aws` against the
  Terraform Registry. The provider's 5.x → 6.x major bump landed in
  mid-2025; 6.x is the actively-maintained major as of 2026-05-10.

---

## 7. Out-of-scope items (deliberately not changed)

- Any `versions.tf`, `wrangler.toml`, worker `package.json`, or workflow
  `uses:` line.
- Re-pinning the 8 float-tag violations to SHAs — separate ticket per
  task scope.
- Bumping wrangler 3 → 4 on embed-worker — separate ticket.
- Extending the `pinned-actions-check.yml` gate's `on:` trigger to
  include `main` — separate ticket. Strongly recommended as a
  same-quarter follow-up because the gate is currently non-enforcing for
  the production branch.

---

*Audit run: 2026-05-10, against origin/main HEAD `b16c78d`.*
