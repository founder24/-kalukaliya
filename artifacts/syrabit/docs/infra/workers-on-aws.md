# Workers on AWS — runbook

**Status:** active (Phase 4 — Task #332)
**Owners:** infra@syrabit.ai
**Pages to:** `aws_sns_topic.ops_alerts` (see `infra/aws/observability.tf`)

This runbook covers the SQS + Lambda async-worker tier that replaced
the GCP Cloud Tasks queues catalogued in
[`docs/infra/inventory/cloud-tasks.json`](inventory/cloud-tasks.json).

---

## Topology

```
┌──────────────┐  enqueue (boto3 send_message)   ┌──────────────┐
│ DO API + Rust│ ───────────────────────────────►│  SQS queue   │
│ producers    │                                 │ syrabit-*    │
└──────────────┘                                 └──────┬───────┘
                                                        │ event-source mapping
                                                        ▼
                                          ┌──────────────────────┐
                                          │ Lambda consumer      │
                                          │ syrabit-*-consumer   │
                                          │ (arm64, image-based) │
                                          └─────┬────────────┬───┘
                                                │            │ partial-batch failures
                                                ▼            ▼
                                       (downstream API)   redrive after N tries
                                                              │
                                                              ▼
                                                    ┌──────────────────┐
                                                    │ syrabit-*-dlq    │
                                                    └──────────────────┘
```

Resources:

| Concern | Terraform file |
| --- | --- |
| Queues + DLQs (8 pairs) | `infra/aws/sqs.tf` |
| Consumer Lambdas + IAM + log groups | `infra/aws/lambda-workers.tf` |
| CloudWatch alarms (backlog / DLQ depth / Lambda errors / composite) | `infra/aws/sqs-alarms.tf` |
| Email-fallback wiring (re-uses existing email-worker Lambda) | `infra/aws/lambda-workers.tf` (`aws_lambda_event_source_mapping.email_fallback`) |
| ops_alerts SNS topic (paging) | `infra/aws/observability.tf` |

Inventory mapping (GCP key → AWS resource) lives in
`docs/infra/inventory/cloud-tasks.json`. Diff against that file in
PR review when a queue is added or retired.

---

## Health surfaces

1. **Composite CloudWatch alarm** `syrabit-workers-degraded` — true
   if any backlog or DLQ alarm is in ALARM state. Polled by the
   admin AWS Infra card via `GET /admin/aws/workers/health`.
2. **Per-queue alarms** — `*-backlog`, `*-dlq-not-empty`,
   `<consumer>-errors`. All page to `ops_alerts`.
3. **CloudWatch Logs** — `/aws/lambda/syrabit-<key>-consumer`
   (14-day retention, X-Ray active tracing on every consumer).
4. **AdminHealth → Infrastructure tab → AWS Infra card** — single
   pane of glass for ops; mirrors the composite alarm + a per-queue
   table (backlog / DLQ depth / consumer error rate / alarm state).

---

## On-call playbook

### Symptom: `*-backlog` alarm fires (queue depth > threshold for 3m)

Most common cause: consumer Lambda hit its **reserved concurrency
ceiling** because a downstream API (Vertex, Bing, IndexNow,
Discovery Engine) is throttling.

1. Open CloudWatch Metrics → `AWS/Lambda` → `Throttles` for
   `syrabit-<key>-consumer`. Non-zero throttles confirm the
   concurrency ceiling.
2. Check the downstream API's status: vendor status page +
   `provider_latency_bench` panel in AdminHealth → Infrastructure.
3. If the downstream is healthy, raise `reserved_concurrent_executions`
   for that consumer in `lambda-workers.tf` (the value is the
   max-parallel cap). Apply via the standard Terraform pipeline.
4. If the downstream is throttling, leave the cap alone — the
   backlog will drain naturally as it recovers; alarm clears with
   `OK` action.

### Symptom: `*-dlq-not-empty` alarm fires

Threshold is 0 — any DLQ message pages. Workflow:

1. Open the DLQ in the SQS console (`syrabit-<key>-dlq`).
2. Use **Start message polling** to inspect the bodies. Look at
   `MessageAttributes` → `RequestId` to grep CloudWatch Logs for
   the original failed invocation.
3. Triage:
   * **Bad payload from a producer.** Drop the messages, file a bug
     against the producer module.
   * **Transient downstream that is now healthy.** Use **Start DLQ
     redrive** in the SQS console (or the AdminHealth "Replay DLQ"
     button which calls `POST /admin/aws/workers/{key}/replay-dlq`).
   * **Persistent consumer bug.** Disable the event-source mapping
     (`enabled = false` in Terraform) so messages stop reaching the
     consumer, then ship a fix.

### Symptom: `<consumer>-errors` alarm fires (5+ errors in 5 min)

This is downstream of either symptom above — usually a real consumer
bug, not a transient blip (those get absorbed by the SQS retry).

1. CloudWatch Logs → filter on `ERROR` over the last 15 min.
2. Cross-check with X-Ray Service Map: `syrabit-<key>-consumer`
   shows red on the failing downstream call.
3. Roll back the consumer image: `docker pull` the previous tag
   from ECR, re-tag as `sqs-consumers-latest`, push, then
   `terraform apply` (the Lambda picks up the new image on next
   cold start; force a refresh by bumping the function's
   `environment.variables.RELEASE_SHA`).

### Symptom: SES email-fallback queue backlogged

Email fallback shares the existing `email_worker` Lambda; backlog
on `syrabit-email-fallback` therefore implies SES itself is throttling.

1. Check SES sending statistics in the AWS Console.
2. If the daily send quota is approaching the cap, file a quota
   increase via the SES console (covered by Activate credits,
   approval is usually < 1 business day).
3. Producers (`notify.py`) already chain CF Workers → Resend → SES,
   so a backlog here means the first two also failed; investigate
   the CF Workers email pipeline first.

---

## Adding a new queue

1. Add a row to `local.sqs_worker_queues` in `infra/aws/sqs.tf`
   (set the GCP key, AWS name, retention, max-receive,
   visibility timeout).
2. Add a matching row to `local.sqs_worker_lambdas` in
   `lambda-workers.tf` (handler path, memory, timeout, concurrency,
   batch size).
3. Add a backlog threshold in `local.sqs_backlog_thresholds` in
   `sqs-alarms.tf` (DLQ + error alarms auto-generate from the same
   key).
4. Add a row to `docs/infra/inventory/cloud-tasks.json` so the
   inventory still matches reality.
5. Implement `services/backend/sqs_consumers/<key>.handler` in the
   backend codebase (keep the handler signature compatible with
   `ReportBatchItemFailures`).

---

## Cost guardrails

* AWS Activate credit covers ~$1 000/yr; the queue+Lambda tier at
  current volume is ~$15/mo.
* `aws_budgets_budget.monthly_cost` (in `account-billing.tf`) trips
  at 50 % / 80 % of $100/mo — investigate any spike.
* SQS pricing is per-million-requests; if you see SQS ApiCalls
  > 10 M/mo for any single queue, the producer is almost certainly
  in a tight loop. Roll back the producer change and reset the
  long-poll interval.

---

## Cutover checklist (one-time, kept for reference)

- [ ] Re-run inventory verification against the live GCP project
      (`docs/infra/inventory/cloud-tasks.json` `verification.command`).
- [ ] `terraform apply` for `sqs.tf`, `lambda-workers.tf`,
      `sqs-alarms.tf` in `infra/aws/`.
- [ ] Smoke-test each consumer by publishing a hand-crafted message
      to the SQS queue from the AWS console; confirm the consumer
      Lambda invocation in CloudWatch.
- [ ] Switch producers from `cloud_tasks_client.send` to
      `sqs_fanout.enqueue` behind a feature flag (`USE_SQS_FANOUT`).
- [ ] Drain the GCP Cloud Tasks queues (pause + wait for empty).
- [ ] Flip the flag in production for 10 % of traffic; monitor the
      composite alarm + CloudWatch error rate for 24h.
- [ ] Ramp to 100 % over the next 48h.
- [ ] Delete the GCP Cloud Tasks queues + the `cloud_tasks_client`
      module; bump the inventory file's `verification.owner_action`
      to "complete".
