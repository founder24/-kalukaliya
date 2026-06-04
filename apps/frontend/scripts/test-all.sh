#!/usr/bin/env bash
# Runs all frontend vitest tests in memory-safe batches.
# Each batch is ~12 files to avoid OOM in constrained environments.
set -euo pipefail

PASS=0
FAIL=0
FAILED_BATCHES=()

run_batch() {
  local label="$1"; shift
  echo ""
  echo "=== $label ==="
  if npx vitest run --reporter=dot "$@"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    FAILED_BATCHES+=("$label")
  fi
}

run_batch "Batch 1: Admin components A" \
  src/components/admin/AdminBotSecurity.test.jsx \
  src/components/admin/AdminConversations.test.jsx \
  "src/components/admin/AdminDashboard.d1SyncExtendedMirror.test.jsx" \
  "src/components/admin/AdminDashboard.kvHealthIsolates.test.jsx" \
  "src/components/admin/AdminDashboard.metricsFreshness.test.jsx" \
  "src/components/admin/AdminDashboard.r2WatchdogReevaluate.test.jsx" \
  "src/components/admin/AdminDashboard.r2WatchdogReset.test.jsx" \
  src/components/admin/AdminEduBrowser.test.js \
  "src/components/admin/AdminHealth.assameseRecent.test.jsx" \
  "src/components/admin/AdminHealth.cooldown.test.jsx" \
  "src/components/admin/AdminHealth.credits.test.jsx" \
  "src/components/admin/AdminHealth.metricsFreshness.test.jsx"

run_batch "Batch 2: Admin components B" \
  "src/components/admin/AdminHealth.signupThrottle.test.jsx" \
  src/components/admin/AdminLogsExplorer.test.jsx \
  src/components/admin/AdminNotifications.test.jsx \
  src/components/admin/AdminRateLimits.test.jsx \
  src/components/admin/AiGatewayCacheByModelTile.test.jsx \
  src/components/admin/AiGatewayGuardrailByModelTile.test.jsx \
  src/components/admin/AlertReasonsRow.test.jsx \
  src/components/admin/BotCachePanel.test.jsx \
  src/components/admin/BreakGlassBanner.test.jsx \
  src/components/admin/CfWafDriftCronPill.test.jsx \
  src/components/admin/cronCaptionHelpers.test.js \
  src/components/admin/CronHealthPill.test.jsx

run_batch "Batch 3: Admin components C + Study" \
  src/components/admin/D1MirrorLagPill.test.jsx \
  "src/components/admin/EdgeMetricsPanel.AlertSettings.test.jsx" \
  src/components/admin/EdgeProxyDeployCronPill.test.jsx \
  src/components/admin/EmbedBackfillPill.test.jsx \
  src/components/admin/EmbedStackHealthPill.test.jsx \
  src/components/admin/R2ColdStoragePanel.test.jsx \
  "src/components/admin/seo-manager/EntitySeoTab.test.jsx" \
  "src/components/admin/seo-manager/LinksTab.test.jsx" \
  src/components/admin/TrustpilotRefreshCronPill.test.jsx \
  src/components/admin/UnifiedLogsCfPullCronPill.test.jsx \
  src/components/content/TrustpilotReviewsSection.test.jsx \
  src/components/study/HighlightSavePopover.test.jsx

run_batch "Batch 4: Study + Pages A" \
  src/components/study/QuizModalPortal.test.jsx \
  src/lib/authErrors.test.js \
  "src/pages/AdminPage.navigation.test.jsx" \
  "src/pages/AdminPage.redirects.test.jsx" \
  src/pages/ChapterPage.axe.test.jsx \
  "src/pages/chat/InputBar.test.jsx" \
  "src/pages/chat/MessageBubble.assamese-unavailable.test.jsx" \
  src/pages/ChatPage.axe.test.jsx \
  "src/pages/chat/RecentMemoriesSection.test.jsx" \
  src/pages/LibraryPage.axe.test.jsx \
  "src/pages/LoginPage.aria.test.jsx" \
  src/pages/LoginPage.axe.test.jsx

run_batch "Batch 5: Pages B + Tests" \
  src/pages/LoginPage.handleSubmit.test.jsx \
  src/pages/MyMemoriesPage.test.jsx \
  src/pages/OnboardingPage.axe.test.jsx \
  src/pages/ProfilePage.axe.test.jsx \
  src/pages/SignupPage.handleSubmit.test.jsx \
  "src/pages/StatusPage.r2Watchdog.test.jsx" \
  src/pages/SubjectLandingPage.axe.test.jsx \
  src/pages/SubjectPage.axe.test.jsx \
  "src/__tests__/deployment-audit.test.jsx" \
  "src/__tests__/latency-comparison.test.js" \
  src/test/post-deploy-lighthouse.test.js

run_batch "Batch 6: Tests + Utils" \
  src/test/prerender-twitter-meta.test.js \
  src/utils/cardContext.test.js \
  src/utils/highlightSegments.test.js \
  src/utils/pushChannelTone.test.js

echo ""
echo "============================================="
echo "Frontend test summary: $PASS batches passed"
if [ $FAIL -gt 0 ]; then
  echo "FAILED batches ($FAIL): ${FAILED_BATCHES[*]}"
  exit 1
else
  echo "All batches passed!"
fi
