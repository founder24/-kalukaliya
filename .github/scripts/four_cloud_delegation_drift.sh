#!/usr/bin/env bash
#
# Mechanical enforcement of the four-cloud delegation matrix
# (infra/four-cloud-delegation.md §B "must NOT" block).
#
# Exit 0 = clean, exit 1 = drift found.
#
# Used by:
#   * .github/workflows/four-cloud-delegation-drift.yml (CI gate)
#   * Local invocation per matrix §C.6 acceptance check
#
# Scope:
#   * Terraform:   infra/**/*.tf, artifacts/syrabit/infra/**/*.tf
#   * Python:      artifacts/syrabit-backend/**/*.py
#   * TypeScript:  workers/**/*.ts
#   * Markdown:    excluded (docs must be free to discuss what's forbidden)
#
# Allow-list policy: each scan() call may pass --allow with a CSV of
# files exempted from that one rule. The two main reasons for an
# exemption are (a) the file is the matrix or landing-zone itself and
# discusses the forbidden pattern in prose, or (b) the file is a
# pre-existing call site that a sibling task is removing in the same
# §15 amendment window (see comments inline for the task #).

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

VIOLATIONS=0

scan() {
  # scan <label> <pattern> --type <ext> <path> [<path>...] [--allow file1,file2,...]
  local label="$1"; shift
  local pattern="$1"; shift
  local file_ext=""
  local allow=""
  local paths=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --type) file_ext="$2"; shift 2 ;;
      --allow) allow="$2"; shift 2 ;;
      *) paths+=("$1"); shift ;;
    esac
  done

  local rg_args=()
  if [ -n "$file_ext" ]; then
    rg_args+=(-g "*.${file_ext}")
  fi
  if [ -n "$allow" ]; then
    IFS=',' read -ra _allows <<< "$allow"
    for f in "${_allows[@]}"; do
      rg_args+=(-g "!${f}")
    done
  fi

  if rg -nH "${rg_args[@]}" -- "$pattern" "${paths[@]}" 2>/dev/null; then
    echo "::error::Four-cloud delegation drift — ${label}"
    echo "         See infra/four-cloud-delegation.md §B for the rule."
    VIOLATIONS=$((VIOLATIONS+1))
  fi
}

# Resolve scan roots that exist (so the gate works on a fresh clone
# even if some directories haven't been created yet).
TF_ROOTS=()
[ -d infra ] && TF_ROOTS+=(infra)
[ -d artifacts/syrabit/infra ] && TF_ROOTS+=(artifacts/syrabit/infra)
PY_ROOT=()
[ -d artifacts/syrabit-backend ] && PY_ROOT+=(artifacts/syrabit-backend)
TS_ROOT=()
[ -d workers ] && TS_ROOT+=(workers)

# ─── GCP hosting / cron / CI / queueing in Terraform ─────────────────────
# All TF scans are scoped --type tf so the matrix MD + landing-zone MD
# can freely list the forbidden patterns in prose without tripping.
if [ "${#TF_ROOTS[@]}" -gt 0 ]; then
  scan "GCP Cloud Run hosting forbidden (V4 §0)" \
       '\bgoogle_cloud_run_' --type tf "${TF_ROOTS[@]}"
  scan "GCP Cloud Functions forbidden (V4 §0)" \
       '\bgoogle_cloudfunctions_' --type tf "${TF_ROOTS[@]}"
  scan "GCP Cloud Tasks forbidden — use AWS SQS (V4 §3)" \
       '\bgoogle_cloud_tasks_' --type tf "${TF_ROOTS[@]}"
  scan "GCP Cloud Scheduler forbidden — use AWS EventBridge (V4 §3)" \
       '\bgoogle_cloud_scheduler_' --type tf "${TF_ROOTS[@]}"
  scan "GCP Compute Engine forbidden — use Azure ACA (V4 §0)" \
       '\bgoogle_compute_(instance|network|subnetwork|disk|firewall)' \
       --type tf "${TF_ROOTS[@]}"
  scan "GKE forbidden — use Azure ACA (V4 §0)" \
       '\bgoogle_container_cluster\b' --type tf "${TF_ROOTS[@]}"
  scan "Cloud Build forbidden — use GitHub Actions (V4 §0)" \
       '\bgoogle_cloudbuild_' --type tf "${TF_ROOTS[@]}"
  scan "GCP Artifact Registry forbidden — image hosting on AWS ECR (V4 §0)" \
       '\bgoogle_artifact_registry_' --type tf "${TF_ROOTS[@]}"
  scan "GCP IAM grant of Cloud Run / Tasks / Scheduler / Build / Functions roles forbidden" \
       '"roles/(run\.|cloudtasks\.|cloudscheduler\.|cloudbuild\.|cloudfunctions\.)' \
       --type tf "${TF_ROOTS[@]}"
  # GCS static-site hosting (`google_storage_bucket` with a `website {…}`
  # block) is forbidden — Cloudflare Pages owns SSR per V4 §0.
  scan "GCS static-site hosting forbidden — Cloudflare Pages owns SSR (V4 §0)" \
       'website\s*=\s*\{|^\s*website\s*\{' --type tf "${TF_ROOTS[@]}"
fi

# ─── AWS FastAPI hosting in Terraform ────────────────────────────────────
if [ "${#TF_ROOTS[@]}" -gt 0 ]; then
  scan "AWS App Runner forbidden — FastAPI lives on Azure ACA (V4 §0)" \
       '\baws_apprunner_' --type tf "${TF_ROOTS[@]}"
  scan "AWS Elastic Beanstalk forbidden — FastAPI lives on Azure ACA (V4 §0)" \
       '\baws_elastic_beanstalk_' --type tf "${TF_ROOTS[@]}"
  # Tightened: only flag actual `resource "aws_ecs_service"` declarations
  # named for the API tier. (Looser patterns matched Front-Door route
  # names + commented-out Route 53 fixtures, which are not ECS.)
  scan "AWS ECS service named for the API tier forbidden — FastAPI lives on Azure ACA (V4 §0)" \
       'resource\s+"aws_ecs_service"\s+"(syrabit_backend|syrabit_api|fastapi)"' \
       --type tf "${TF_ROOTS[@]}"
fi

# ─── Re-introduction of deleted GCP client modules in Python ──────────────
if [ "${#PY_ROOT[@]}" -gt 0 ]; then
  scan "cloud_tasks_client deleted by Task #489 — use sqs_fanout.enqueue (AWS SQS)" \
       '^\s*(from|import)\s+cloud_tasks_client\b' \
       --type py "${PY_ROOT[@]}"
  scan "cloud_scheduler_client deleted by Task #489 — use AWS EventBridge schedules" \
       '^\s*(from|import)\s+cloud_scheduler_client\b' \
       --type py "${PY_ROOT[@]}"
fi

# ─── Retired AI providers in Python (V4 §15 amendment) ───────────────────
# Cohere + Voyage exemptions: providers/cohere.py + providers/voyage_ai.py
# + config.py predate the §15 amendment. Sibling task #491 removes them;
# until #491 lands the existing files are explicitly allowed so this gate
# does not block unrelated PRs. Once #491 lands, drop the --allow entries
# and the gate will catch any new re-introduction.
if [ "${#PY_ROOT[@]}" -gt 0 ]; then
  scan "Vertex Gemini chat forbidden on hot path — Azure OpenAI is sole primary (V4 §4)" \
       '^\s*(from|import)\s+(vertexai|google\.cloud\.aiplatform).*generative_models' \
       --type py "${PY_ROOT[@]}" \
       --allow "artifacts/syrabit-backend/vertex_content_formatter.py"

  scan "Vertex multilingual embedding retired by sibling #490 — embedder is Cloudflare Workers AI only (V4 §2 + §15)" \
       'text-multilingual-embedding-' \
       --type py "${PY_ROOT[@]}" \
       --allow "artifacts/syrabit-backend/gcp_billing.py"

  scan "Vertex Vector Search retired by sibling #490 — Pinecone Rerank + RRF (V4 §2 + §15)" \
       'vertexai\.matching_engine|MatchingEngineIndex' \
       --type py "${PY_ROOT[@]}"

  scan "Cohere retired by sibling #491 — no NEW embed/chat/rerank dependency on Cohere (V4 §15)" \
       '^\s*(from|import)\s+cohere\b|api\.cohere\.com' \
       --type py "${PY_ROOT[@]}" \
       --allow "artifacts/syrabit-backend/providers/cohere.py,artifacts/syrabit-backend/config.py"

  scan "Voyage AI retired by sibling #491 — no NEW embed/rerank dependency on Voyage (V4 §15)" \
       '^\s*(from|import)\s+voyageai\b|api\.voyageai\.com' \
       --type py "${PY_ROOT[@]}" \
       --allow "artifacts/syrabit-backend/providers/voyage_ai.py,artifacts/syrabit-backend/config.py"

  # Cerebras must never appear as a chat-primary slot. The CF AI Gateway
  # `cerebras` slug exists for telemetry parity (matrix §A "Edge AI
  # dispatch / BYOK gateway"), but new code MUST NOT pin the chat hot
  # path to it. Catch the obvious patterns: `provider="cerebras"` /
  # `cerebras_chat(`. Allow-list the existing pre-#491 provider
  # adapter file and config.py.
  # Cerebras as chat-primary is forbidden, but the matrix §A "Edge AI
  # dispatch / BYOK gateway" row explicitly permits the `cerebras` slug
  # for AI-Gateway telemetry parity. `llm.py` carries the recording
  # helpers (`_record_aig_from_raw/stream`) and is allow-listed; the
  # gate still catches NEW chat-primary pins anywhere else (e.g. a new
  # route or dispatcher module).
  scan "Cerebras forbidden as chat-primary — Azure OpenAI is sole primary (V4 §4 + §15)" \
       'cerebras_chat\(|"cerebras"\s*:\s*\{[^}]*"primary"\s*:\s*true' \
       --type py "${PY_ROOT[@]}" \
       --allow "artifacts/syrabit-backend/providers/cerebras.py,artifacts/syrabit-backend/config.py,artifacts/syrabit-backend/llm.py"
fi

# Same retired-providers checks against TS edge code (no pre-existing
# allow-list — the worker tree never imported these directly).
if [ "${#TS_ROOT[@]}" -gt 0 ]; then
  scan "Vertex multilingual embedding retired (TS edge)" \
       'text-multilingual-embedding-' --type ts "${TS_ROOT[@]}"
  scan "Cohere retired (TS edge)" \
       'api\.cohere\.com' --type ts "${TS_ROOT[@]}"
  scan "Voyage AI retired (TS edge)" \
       'api\.voyageai\.com' --type ts "${TS_ROOT[@]}"
fi

if [ "$VIOLATIONS" -gt 0 ]; then
  echo ""
  echo "Found ${VIOLATIONS} four-cloud delegation drift violation(s)."
  echo "See infra/four-cloud-delegation.md §B for the negative-space rules."
  exit 1
fi

echo "OK: no four-cloud delegation drift found"
