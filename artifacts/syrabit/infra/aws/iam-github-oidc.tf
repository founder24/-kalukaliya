# infra/aws/iam-github-oidc.tf
#
# Phase 1b — AWS landing zone (Task #328).
#
# Identity baseline for the AWS landing zone:
#
# 1. GitHub OIDC provider — lets the `aws-deploy-workers.yml` workflow
#    in the syrabit GitHub repo assume an AWS role without a long-lived
#    access key.
# 2. Deploy role — what GitHub Actions assumes; scoped to the resources
#    the worker tier deploy actually touches (Lambda, ECR, IAM PassRole
#    for the runtime role, CloudWatch logs).
# 3. Runtime role — what worker Lambdas / Fargate tasks assume at run
#    time; scoped to the specific Secrets Manager ARNs, SQS queues, and
#    SES identity the worker tier uses.
#
# These roles are intentionally split so a compromised CI runner cannot
# read application secrets, and a compromised worker cannot redeploy
# itself.

variable "github_owner" {
  description = "GitHub org/user that owns the syrabit repo."
  type        = string
  default     = "syrabit"
}

variable "github_repo" {
  description = "GitHub repository name (without org)."
  type        = string
  default     = "syrabit"
}

# ─── 1. GitHub OIDC provider ─────────────────────────────────────────────────

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  tags = local.lz_common_tags
}

# ─── 2. Deploy role (assumed by GitHub Actions) ──────────────────────────────

data "aws_iam_policy_document" "github_deploy_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Restrict to the correct repo on master/release branches, named
    # environments, and workflow_dispatch (any ref).  The wildcard entry
    # covers `workflow_dispatch` runs whose sub is
    # `repo:OWNER/REPO:ref:refs/heads/BRANCH` — same pattern as push
    # triggers, so no extra entry is needed beyond the ref patterns.
    # Previously this block used hardcoded "syrabit"/"syrabit" defaults;
    # terraform.tfvars now sets github_owner="founder24",
    # github_repo="-kalukaliya" (the actual repo).
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/master",
        "repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/main",
        "repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/release/*",
        "repo:${var.github_owner}/${var.github_repo}:environment:prod",
        "repo:${var.github_owner}/${var.github_repo}:environment:staging",
      ]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${local.lz_project}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_deploy_assume.json
  description        = "Assumed by aws-deploy-workers.yml GitHub Actions workflow."

  tags = local.lz_common_tags
}

data "aws_caller_identity" "lz" {}

data "aws_iam_policy_document" "github_deploy_perms" {
  # ECR push/pull for worker images.
  statement {
    sid    = "EcrAuth"
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "EcrPush"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:BatchGetImage",
      "ecr:DescribeRepositories",
      "ecr:DescribeImages",
      "ecr:ListImages",
    ]
    resources = [
      "arn:aws:ecr:${local.lz_primary_region}:${data.aws_caller_identity.lz.account_id}:repository/${local.lz_project}/*",
    ]
  }

  # Lambda — update existing functions (code-deploy path) AND create/delete
  # new functions (bootstrap path, i.e. lambda-bootstrap workflow).
  statement {
    sid    = "LambdaDeploy"
    effect = "Allow"
    actions = [
      "lambda:GetFunction",
      "lambda:ListFunctions",
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PublishVersion",
      "lambda:UpdateAlias",
      "lambda:GetAlias",
      "lambda:CreateAlias",
      "lambda:DeleteAlias",
      "lambda:ListVersionsByFunction",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:GetPolicy",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
    ]
    resources = [
      "arn:aws:lambda:${local.lz_primary_region}:${data.aws_caller_identity.lz.account_id}:function:${local.lz_project}-*",
      "arn:aws:lambda:${local.lz_secondary_region}:${data.aws_caller_identity.lz.account_id}:function:${local.lz_project}-*",
    ]
  }

  # Lambda event source mappings (SQS triggers).
  statement {
    sid    = "LambdaESM"
    effect = "Allow"
    actions = [
      "lambda:CreateEventSourceMapping",
      "lambda:DeleteEventSourceMapping",
      "lambda:UpdateEventSourceMapping",
      "lambda:GetEventSourceMapping",
      "lambda:ListEventSourceMappings",
    ]
    resources = ["*"]
  }

  # EventBridge Scheduler — create/manage batch-job schedules.
  statement {
    sid    = "SchedulerDeploy"
    effect = "Allow"
    actions = [
      "scheduler:CreateSchedule",
      "scheduler:UpdateSchedule",
      "scheduler:DeleteSchedule",
      "scheduler:GetSchedule",
      "scheduler:ListSchedules",
      "scheduler:TagResource",
      "scheduler:UntagResource",
    ]
    resources = [
      "arn:aws:scheduler:${local.lz_primary_region}:${data.aws_caller_identity.lz.account_id}:schedule/default/${local.lz_project}-*",
    ]
  }

  # CloudWatch log-group creation for Lambda functions.
  statement {
    sid    = "LogGroupCreate"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DeleteLogGroup",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
    ]
    resources = [
      "arn:aws:logs:${local.lz_primary_region}:${data.aws_caller_identity.lz.account_id}:log-group:/aws/lambda/${local.lz_project}-*",
      "arn:aws:logs:${local.lz_primary_region}:${data.aws_caller_identity.lz.account_id}:log-group:/aws/lambda/${local.lz_project}-*:*",
      "arn:aws:logs:${local.lz_secondary_region}:${data.aws_caller_identity.lz.account_id}:log-group:/aws/lambda/${local.lz_project}-*",
      "arn:aws:logs:${local.lz_secondary_region}:${data.aws_caller_identity.lz.account_id}:log-group:/aws/lambda/${local.lz_project}-*:*",
      aws_cloudwatch_log_group.workers.arn,
      "${aws_cloudwatch_log_group.workers.arn}:*",
    ]
  }

  # PassRole — only the runtime role; deploy cannot pass arbitrary roles.
  statement {
    sid       = "PassRuntimeRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.workers_runtime.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "deploy-perms"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy_perms.json
}

# ─── 3. Runtime role (assumed by worker Lambdas / Fargate tasks) ─────────────

resource "aws_iam_role" "workers_runtime" {
  name        = "${local.lz_project}-workers-runtime"
  description = "Runtime role for async worker Lambdas / Fargate tasks."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = [
            "lambda.amazonaws.com",
            "ecs-tasks.amazonaws.com",
          ]
        }
        Action = "sts:AssumeRole"
      },
    ]
  })

  tags = local.lz_common_tags
}

resource "aws_iam_role_policy_attachment" "workers_runtime_basic" {
  role       = aws_iam_role.workers_runtime.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "workers_runtime_vpc" {
  role       = aws_iam_role.workers_runtime.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "workers_runtime" {
  # Read only the secrets the worker tier owns.
  statement {
    sid       = "SecretsRead"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [for s in aws_secretsmanager_secret.workers : s.arn]
  }

  # Receive/process from worker SQS queues (queues themselves are created
  # in Phase 4 — wildcard is scoped to the project namespace).
  statement {
    sid    = "SqsConsume"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ChangeMessageVisibility",
      "sqs:SendMessage",
    ]
    resources = [
      "arn:aws:sqs:${local.lz_primary_region}:${data.aws_caller_identity.lz.account_id}:${local.lz_project}-*",
    ]
  }

  # Send via the verified SES domain identity only.
  statement {
    sid    = "SesSend"
    effect = "Allow"
    actions = [
      "ses:SendEmail",
      "ses:SendRawEmail",
    ]
    resources = [
      aws_sesv2_email_identity.syrabit_ai.arn,
      aws_sesv2_configuration_set.workers.arn,
    ]
  }

  # SES quota / statistics are account-level reads — they cannot be
  # scoped to an identity ARN. Kept in a separate statement so the
  # send permissions above stay tightly scoped.
  statement {
    sid    = "SesAccountReads"
    effect = "Allow"
    actions = [
      "ses:GetSendStatistics",
      "ses:GetSendQuota",
      "ses:GetAccount",
    ]
    resources = ["*"]
  }

  # Publish CloudWatch metrics to the project namespace and write to the
  # shared landing-zone log group.
  statement {
    sid       = "MetricsPut"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Syrabit/Workers"]
    }
  }
}

resource "aws_iam_role_policy" "workers_runtime" {
  name   = "runtime-perms"
  role   = aws_iam_role.workers_runtime.id
  policy = data.aws_iam_policy_document.workers_runtime.json
}

# ─── Outputs ─────────────────────────────────────────────────────────────────

output "github_deploy_role_arn" {
  value       = aws_iam_role.github_deploy.arn
  description = "Set as AWS_ROLE_ARN secret in the syrabit repo's GitHub env."
}

output "workers_runtime_role_arn" {
  value       = aws_iam_role.workers_runtime.arn
  description = "Pass to Lambda/Fargate worker definitions in Phase 4."
}
