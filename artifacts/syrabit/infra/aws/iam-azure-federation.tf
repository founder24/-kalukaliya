# infra/aws/iam-azure-federation.tf
#
# Phase 4 — Cross-cloud auth (Task #332).
#
# OIDC trust between Azure (cron jobs' user-assigned managed
# identity) and AWS so the Container Apps Jobs can call SQS,
# CloudWatch, and Lambda without a long-lived access key. Mirrors
# the GitHub-OIDC pattern already in `iam-github-oidc.tf` so we
# keep one auth posture across all callers.
#
# Trust chain:
#   1. Azure issues a federated token for the user-assigned managed
#      identity `syrabit-cron-jobs-runtime` (see
#      `infra/azure/iam-github-oidc.tf`).
#   2. AWS verifies the token via the Azure tenant's OIDC issuer
#      (`https://login.microsoftonline.com/<tenant-id>/v2.0`).
#   3. AssumeRoleWithWebIdentity returns short-lived (≤1h) STS
#      credentials scoped to `syrabit-cron-jobs-aws` (this role).
#
# Bootstrap: a one-shot init container in each ACA Job (declared in
# `container-apps-jobs.tf > init-containers.tf`) writes the federated
# token to `AWS_WEB_IDENTITY_TOKEN_FILE` and exports `AWS_ROLE_ARN`
# pointing at this role's ARN. boto3 then auto-detects the federated
# session — no static credentials anywhere in the runtime path.

variable "azure_tenant_id" {
  description = "Azure tenant ID hosting the cron-jobs managed identity. Set in tfvars."
  type        = string
}

variable "azure_cron_runtime_object_id" {
  description = "objectId of the user-assigned managed identity `syrabit-cron-jobs-runtime`. Output of `azurerm_user_assigned_identity.cron_jobs_runtime.principal_id`."
  type        = string
}

resource "aws_iam_openid_connect_provider" "azure_cron" {
  url             = "https://login.microsoftonline.com/${var.azure_tenant_id}/v2.0"
  client_id_list  = ["api://AzureADTokenExchange"]
  thumbprint_list = ["626d44e704d1ceabe3bf0d53397464ac8080142c"] # Microsoft signing cert; rotate via terraform when MS rotates

  tags = merge(local.lz_common_tags, {
    component = "cross-cloud-auth"
  })
}

data "aws_iam_policy_document" "cron_jobs_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.azure_cron.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "login.microsoftonline.com/${var.azure_tenant_id}/v2.0:sub"
      values   = [var.azure_cron_runtime_object_id]
    }
    condition {
      test     = "StringEquals"
      variable = "login.microsoftonline.com/${var.azure_tenant_id}/v2.0:aud"
      values   = ["api://AzureADTokenExchange"]
    }
  }
}

resource "aws_iam_role" "cron_jobs_aws" {
  name               = "${local.lz_project}-cron-jobs-aws"
  assume_role_policy = data.aws_iam_policy_document.cron_jobs_assume_role.json
  max_session_duration = 3600

  tags = merge(local.lz_common_tags, {
    component = "cross-cloud-auth"
    purpose   = "Federated SQS+CloudWatch+Lambda access for ACA cron jobs"
  })
}

# Scoped to ONLY the SQS queues + Lambda functions + CloudWatch
# metrics that the cron jobs actually need. Add explicit ARNs over
# wildcards so a future queue addition is visible in the diff.
data "aws_iam_policy_document" "cron_jobs_aws_perms" {
  statement {
    sid     = "EnqueueAndAdminListing"
    effect  = "Allow"
    actions = [
      "sqs:SendMessage",
      "sqs:SendMessageBatch",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ReceiveMessage",       # for DLQ replay
      "sqs:DeleteMessage",        # for DLQ replay
    ]
    resources = concat(
      [for q in aws_sqs_queue.worker     : q.arn],
      [for q in aws_sqs_queue.worker_dlq : q.arn],
    )
  }
  statement {
    sid       = "ReadCloudWatchForAdminCard"
    effect    = "Allow"
    actions   = ["cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:DescribeAlarms"]
    resources = ["*"]   # CW metric reads are not resource-scoped
  }
}

resource "aws_iam_policy" "cron_jobs_aws" {
  name        = "${local.lz_project}-cron-jobs-aws"
  description = "Least-privilege SQS + CloudWatch grants for ACA cron jobs (Task #332)."
  policy      = data.aws_iam_policy_document.cron_jobs_aws_perms.json
}

resource "aws_iam_role_policy_attachment" "cron_jobs_aws" {
  role       = aws_iam_role.cron_jobs_aws.name
  policy_arn = aws_iam_policy.cron_jobs_aws.arn
}

output "cron_jobs_aws_role_arn" {
  value       = aws_iam_role.cron_jobs_aws.arn
  description = "Role ARN consumed by the cross-cloud init container; mirrored into Azure as the AWS_ROLE_ARN env on every Container Apps Job."
}
