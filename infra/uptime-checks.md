# External Uptime Monitoring Configuration

## Recommended Tools

- **UptimeRobot** (free tier): 5-minute check intervals, 50 monitors free, email/Slack/webhook alerts
- **Checkly** (API monitoring): 1-minute checks, API assertions, Playwright-based browser checks

## Endpoints to Monitor

| # | URL | Description | Expected Response |
|---|-----|-------------|-------------------|
| 1 | `https://api.syrabit.ai/health` | Backend health check | HTTP 200, JSON with `status: "healthy"` |
| 2 | `https://syrabit.ai` | Frontend availability | HTTP 200 |
| 3 | `https://api.syrabit.ai/api/content/boards` | Content API | HTTP 200, verifies DB connectivity |

## Check Intervals

- Health endpoint: every 1 minute (critical path)
- Frontend: every 3 minutes
- Content API: every 5 minutes

Recommended range: **1-5 minutes** depending on endpoint criticality.

## Alert Channels

| Channel | Target | Notes |
|---------|--------|-------|
| Email | team@syrabit.ai | Primary notification |
| Slack webhook | #alerts channel | Real-time team visibility |
| Discord webhook | #alerts channel | Secondary notification |

## Escalation Policy

- **Down > 5 minutes**: Page on-call engineer via PagerDuty/Opsgenie
- **Down > 15 minutes**: Escalate to engineering lead
- **Down > 30 minutes**: Incident commander engaged, status page updated
