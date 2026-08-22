#!/usr/bin/env bash
# =============================================================================
# setup-d1.sh — Create and configure the Cloudflare D1 database for Syrabit
#
# Run once when setting up the Cloudflare migration for the first time.
# Requires: wrangler CLI authenticated (wrangler login)
#
# Usage:
#   bash scripts/setup-d1.sh
# =============================================================================

set -euo pipefail

WORKER_DIR="apps/api"
DB_NAME="syrabit-db"

echo "🗄️  Creating D1 database: $DB_NAME ..."
DB_OUTPUT=$(cd "$WORKER_DIR" && pnpm exec wrangler d1 create "$DB_NAME" 2>&1) || true
echo "$DB_OUTPUT"

# Extract the database_id from wrangler output
DB_ID=$(echo "$DB_OUTPUT" | grep -oP 'database_id\s*=\s*"\K[^"]+' || true)

if [[ -z "$DB_ID" ]]; then
  # Database may already exist — try to get its ID
  echo "ℹ️  Trying to fetch existing database ID..."
  DB_ID=$(cd "$WORKER_DIR" && pnpm exec wrangler d1 list --json 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); match=[x for x in d if x.get('name')=='$DB_NAME']; print(match[0]['uuid'] if match else '')" 2>/dev/null || true)
fi

if [[ -z "$DB_ID" ]]; then
  echo "❌ Could not determine D1 database ID. Please create it manually:"
  echo "   cd apps/api && wrangler d1 create $DB_NAME"
  echo "   Then update wrangler.toml with the database_id."
  exit 1
fi

echo "✅ D1 database ID: $DB_ID"

# Patch wrangler.toml with the real database ID
echo "📝 Updating apps/api/wrangler.toml ..."
sed -i "s/REPLACE_WITH_D1_ID/$DB_ID/g" "$WORKER_DIR/wrangler.toml"
echo "✅ wrangler.toml updated"

# Apply initial migration
echo "🔄 Applying initial schema migration (local) ..."
cd "$WORKER_DIR" && pnpm exec wrangler d1 migrations apply "$DB_NAME" --local
echo "✅ Local migration complete"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ D1 setup complete! Next steps:"
echo ""
echo "1. Apply migration to production:"
echo "   cd apps/api && wrangler d1 migrations apply $DB_NAME --remote --env production"
echo ""
echo "2. Set required secrets:"
echo "   cd apps/api"
echo "   wrangler secret put JWT_SECRET --env production"
echo "   wrangler secret put ADMIN_JWT_SECRET --env production"
echo "   wrangler secret put RESET_TOKEN_SECRET --env production"
echo "   wrangler secret put EDGE_SHARED_SECRET --env production"
echo "   wrangler secret put RAZORPAY_KEY_ID --env production"
echo "   wrangler secret put RAZORPAY_KEY_SECRET --env production"
echo "   wrangler secret put RAZORPAY_WEBHOOK_SECRET --env production"
echo "   wrangler secret put RESEND_API_KEY --env production"
echo "   wrangler secret put SARVAM_API_KEY --env production"
echo "   wrangler secret put GEMINI_API_KEY --env production"
echo ""
echo "3. Deploy the API Worker:"
echo "   cd apps/api && wrangler deploy --env production"
echo ""
echo "4. Test health endpoint:"
echo "   curl https://syrabit-api-prod.<account>.workers.dev/health"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
