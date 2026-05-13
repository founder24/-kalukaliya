#!/usr/bin/env bash
# Task #360 Step 11 — CI grep-gate for removed providers.
#
# Fails the build if any forbidden provider name appears in NEW active
# Python code paths under artifacts/syrabit-backend/. Removals tracked
# in #347. Allowed:
#   - tests, docs, comments mentioning #347 / removed / decommiss / legacy
#   - existing admin frontend Stripe-currency display code
#   - the guard module that *defines* the forbidden constant
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Tokens forbidden in active backend Python code. Each entry is a
# regex passed to ripgrep with -i (case-insensitive). The list mirrors
# the dead-providers blocklist in `scripts/check_dead_providers.py` and
# the v3 provider-priority map (`infra/provider-priority-map.md`):
# anthropic, direct OpenAI client (use Azure OpenAI), Together,
# HuggingFace inference, Ollama, Bedrock, xAI, Grok, the legacy Quge5
# vendor, and the Resend mailer (use SendGrid→SES per #360).
FORBIDDEN_BACKEND=(
  '\bimport openai\b'
  '\bfrom openai\b'
  '\bimport anthropic\b'
  '\bfrom anthropic\b'
  '\bimport together\b'
  '\bfrom together\b'
  '\bhuggingface_hub\.InferenceClient\b'
  '\bollama\.chat\b'
  '\bbedrock_runtime\b'
  '\bquge5\b'
  '\bresend_api\b'
  '\b@xai\b'
  '\bgrok-?[0-9]'
)

EXCLUDES=(
  '--glob=!**/__pycache__/**'
  '--glob=!**/tests/**'
  '--glob=!**/test_*.py'
  '--glob=!**/CHANGELOG*'
  '--glob=!.local/**'
  '--glob=!infra/**'
  '--glob=!docs/**'
  # Vendored third-party SDK; not part of our active code paths.
  '--glob=!**/emergentintegrations/**'
  # Existing legacy-provider auditors — their job is to mention these
  # names. New active dispatch code is what we're guarding here.
  '--glob=!**/scripts/check_dead_providers.py'
  '--glob=!**/scripts/ci/check_canonical_delegation.py'
)

# Round-3 review tightening: NO comment-word bypass. The only way a
# forbidden token can appear in active backend code is to be on an
# explicit per-pattern path allowlist. `noqa` / `# legacy` / `removed`
# comments no longer suppress provider-token hits anywhere.
#
# Allowlist format:  PATTERN_ALLOWLIST["<pattern>"]="path1|path2|…"
# (regex anchored against the start of `<filepath>:<line>:` ripgrep
# output). Empty = no allowlist for that pattern.
declare -A PATTERN_ALLOWLIST=(
  # The legacy AsyncOpenAI client SDK is reused as the HTTP transport
  # for Azure OpenAI / Workers AI / CF AI Gateway only — never
  # `api.openai.com`. Allowed in `llm.py` ONLY. Any new file that
  # imports `openai` will fail this gate.
  ['\bimport openai\b']='artifacts/syrabit-backend/llm\.py'
  ['\bfrom openai\b']='artifacts/syrabit-backend/llm\.py'
)

fail=0
echo "[ci_grep_gate] scanning artifacts/syrabit-backend/ for forbidden imports…"
# Pure-comment lines (where the forbidden token only appears after a
# `#` on the line — i.e. inside a Python comment) are NOT active code
# and do not trip the gate. Active-code lines that put the token in
# real Python and then add a `# noqa`/`# legacy` suppression comment
# on the same line ARE caught, because stripping the `#…` tail still
# leaves the forbidden token in the code portion. This is the round-3
# reviewer requirement: "no comment-word bypass for active code".
for pat in "${FORBIDDEN_BACKEND[@]}"; do
  raw=$(rg -i --no-heading -n "${EXCLUDES[@]}" -e "$pat" \
            artifacts/syrabit-backend/ 2>/dev/null || true)
  [ -z "$raw" ] && continue
  # Strip everything from the first `#` to end-of-line, then re-test
  # the pattern. If the token is gone, the only match was inside a
  # Python comment → not active code.
  code_hits=$(echo "$raw" | python3 -c "
import re, sys
pat = re.compile(r'''$pat''', re.IGNORECASE)
for line in sys.stdin:
    line = line.rstrip('\n')
    parts = line.split(':', 2)
    if len(parts) < 3:
        continue
    src = parts[2]
    code = src.split('#', 1)[0]
    if pat.search(code):
        print(line)
" || true)
  allow_re="${PATTERN_ALLOWLIST[$pat]:-}"
  if [ -n "$allow_re" ] && [ -n "$code_hits" ]; then
    code_hits=$(echo "$code_hits" | grep -v -E "^($allow_re):" || true)
  fi
  if [ -n "$code_hits" ]; then
    echo "::error::forbidden token /$pat/ in active backend code (no comment bypass):"
    echo "$code_hits"
    fail=1
  fi
done

# gpt-oss-120b: forbidden in the FastAPI live-chat handler module(s).
# Round-4 reviewer requirement: NO comment-word bypass. Strip the
# `#…` comment tail in Python and re-test the regex against the code
# portion alone — pure-comment lines pass, active-code lines do not.
echo "[ci_grep_gate] scanning live-chat handlers for gpt-oss-120b dispatch…"
LIVE_CHAT_GLOBS=(
  'artifacts/syrabit-backend/routes/ai_chat.py'
  'artifacts/syrabit-backend/routes/chat.py'
)
for f in "${LIVE_CHAT_GLOBS[@]}"; do
  [ -f "$f" ] || continue
  raw=$(rg -n 'gpt-oss-120b' "$f" 2>/dev/null || true)
  [ -z "$raw" ] && continue
  hits=$(echo "$raw" | python3 -c "
import re, sys
pat = re.compile(r'gpt-oss-120b', re.IGNORECASE)
for line in sys.stdin:
    line = line.rstrip('\n')
    parts = line.split(':', 1)
    if len(parts) < 2:
        continue
    src = parts[1]
    code = src.split('#', 1)[0]
    if pat.search(code):
        print(line)
" || true)
  if [ -n "$hits" ]; then
    echo "::error::gpt-oss-120b referenced in active code of live-chat handler $f:"
    echo "$hits"
    fail=1
  fi
done

# Python ↔ Rust boundary: chat handlers must not synchronously call
# the rust-core HTTP API. SQS / EventBridge enqueues are allowed.
# Same comment-stripping discipline as the gpt-oss-120b check.
echo "[ci_grep_gate] scanning chat handlers for direct rust-core calls…"
CHAT_HANDLERS=$(find artifacts/syrabit-backend/routes -maxdepth 1 -type f -name '*chat*.py' 2>/dev/null || true)
for f in $CHAT_HANDLERS; do
  raw=$(rg -n 'rust[_-]core|RUST_CORE_URL|/rust/' "$f" 2>/dev/null || true)
  [ -z "$raw" ] && continue
  hits=$(echo "$raw" | python3 -c "
import re, sys
pat = re.compile(r'rust[_-]core|RUST_CORE_URL|/rust/', re.IGNORECASE)
async_ok = re.compile(r'sqs|eventbridge|enqueue', re.IGNORECASE)
for line in sys.stdin:
    line = line.rstrip('\n')
    parts = line.split(':', 1)
    if len(parts) < 2:
        continue
    src = parts[1]
    code = src.split('#', 1)[0]
    if not pat.search(code):
        continue
    # Async enqueue paths (SQS/EventBridge) are explicitly allowed.
    if async_ok.search(code):
        continue
    print(line)
" || true)
  if [ -n "$hits" ]; then
    echo "::error::synchronous rust-core call in chat handler: $f"
    echo "$hits"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "::error::ci_grep_gate failed — see hits above"
  exit 1
fi
echo "[ci_grep_gate] OK — no forbidden references found"
