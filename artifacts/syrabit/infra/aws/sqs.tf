# infra/aws/sqs.tf
#
# Phase 4 — Async worker port (Task #332).
#
# SQS queues that replace the GCP Cloud Tasks queues catalogued in
# `docs/infra/inventory/cloud-tasks.json`. Each producer queue gets
# a same-named DLQ; messages that fail `max_receive_count` deliveries
# land there for human triage instead of looping forever and burning
# both Activate credit and Lambda concurrency.
#
# Queue list intentionally mirrors the inventory 1-for-1 (same key,
# same target_lambda) so the cutover boils down to swapping the
# producer client (cloud_tasks_client.send → sqs_fanout.send) with
# zero name-mapping translation in the consumer routes.

locals {
  # Map keyed by the GCP Cloud Tasks queue name (the "name" column in
  # cloud-tasks.json). The string values are the AWS-side resource
  # names; keeping the GCP key here means the inventory file remains
  # the single source of truth — diffs against it are visible in PRs.
  sqs_worker_queues = {
    "seo-indexnow"           = { aws = "syrabit-seo-indexnow",        max_receive = 5,  visibility_timeout = 60,  retention_days = 4  }
    "seo-internal-linker"    = { aws = "syrabit-seo-internal-linker", max_receive = 5,  visibility_timeout = 120, retention_days = 4  }
    "discovery-engine-ingest"= { aws = "syrabit-discovery-ingest",    max_receive = 5,  visibility_timeout = 180, retention_days = 4  }
    "bing-keyword-refresh"   = { aws = "syrabit-bing-keyword",        max_receive = 3,  visibility_timeout = 300, retention_days = 7  }
    "bing-submit"            = { aws = "syrabit-bing-submit",         max_receive = 5,  visibility_timeout = 60,  retention_days = 4  }
    "cf-bot-crosscheck"      = { aws = "syrabit-cf-bot-crosscheck",   max_receive = 3,  visibility_timeout = 60,  retention_days = 2  }
    "unified-logs-cf-pull"   = { aws = "syrabit-unified-logs-pull",   max_receive = 3,  visibility_timeout = 300, retention_days = 2  }
    "email-fallback"         = { aws = "syrabit-email-fallback",      max_receive = 5,  visibility_timeout = 60,  retention_days = 14 }
  }
}

# ─── Dead-letter queues ──────────────────────────────────────────────────────
# Created first so the primary queues can reference them by ARN. 14-day
# retention matches AWS default and gives weekend-shift on-call enough
# headroom to drain manually before messages expire.

resource "aws_sqs_queue" "worker_dlq" {
  for_each = local.sqs_worker_queues

  name                       = "${each.value.aws}-dlq"
  message_retention_seconds  = 14 * 24 * 3600
  visibility_timeout_seconds = each.value.visibility_timeout
  sqs_managed_sse_enabled    = true

  tags = merge(local.lz_common_tags, {
    Name      = "${each.value.aws}-dlq"
    component = "sqs-worker-dlq"
    gcp-key   = each.key
  })
}

# ─── Primary queues ──────────────────────────────────────────────────────────

resource "aws_sqs_queue" "worker" {
  for_each = local.sqs_worker_queues

  name                       = each.value.aws
  message_retention_seconds  = each.value.retention_days * 24 * 3600
  visibility_timeout_seconds = each.value.visibility_timeout
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.worker_dlq[each.key].arn
    maxReceiveCount     = each.value.max_receive
  })

  tags = merge(local.lz_common_tags, {
    Name      = each.value.aws
    component = "sqs-worker"
    gcp-key   = each.key
  })
}

# ─── Outputs ─────────────────────────────────────────────────────────────────
# Consumed by the backend Terraform-managed config (SSM parameters in
# secrets.tf) so producer code reads queue URLs from a single source.

output "sqs_worker_queue_urls" {
  description = "Map of GCP Cloud Tasks key → SQS queue URL. Producers read this via SSM."
  value       = { for k, q in aws_sqs_queue.worker : k => q.url }
}

output "sqs_worker_queue_arns" {
  description = "Map of GCP Cloud Tasks key → SQS queue ARN. Used by Lambda event-source mappings."
  value       = { for k, q in aws_sqs_queue.worker : k => q.arn }
}

output "sqs_worker_dlq_arns" {
  description = "Map of GCP Cloud Tasks key → DLQ ARN. Used by CloudWatch alarms."
  value       = { for k, q in aws_sqs_queue.worker_dlq : k => q.arn }
}
