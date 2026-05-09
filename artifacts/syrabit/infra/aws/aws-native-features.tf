# infra/aws/aws-native-features.tf
#
# Task #337 — Enable AWS-native advanced features for Syrabit.
#
# This file lights up the additional AWS managed AI / utility services
# that ride on the existing landing zone (`account-billing.tf`,
# `network.tf`, `secrets.tf`). Every service here is wired as an
# *additional* provider in the existing failover chains — nothing is
# replaced. See `docs/features/aws-native.md` for the runbook.
#
# Hosting-plan alignment
# ──────────────────────
# Per `docs/infra/cloud-allocation-plan.md` §6 + §9, **Bedrock is
# Cohere-only** in the four-cloud architecture: `embed-multilingual-v3`
# and `rerank-v3.5`. Anthropic Claude / Meta Llama / Mistral / Amazon
# Titan / Nova on Bedrock are explicitly out of scope — Azure OpenAI
# (GPT-4.1-mini) and Vertex Gemini 2.5 Flash cover those LLM roles per
# the credit matrix. The legacy `lambda-bedrock-proxy.tf` Claude+Titan
# IAM policy is left in place for the in-flight decommission window
# but no new code paths target it.
#
# Calling pattern
# ───────────────
# The Python backend on Azure Container Apps and the Lambda workers on AWS
# both call these services via short-lived role assumption — no static
# AWS access keys are issued. Each role is least-privilege and tagged
# `feature=<service>` so Cost Explorer can break down spend per
# AWS-native feature on the admin billing panel.

# ─── Shared assume-role document for cross-account callers ──────────
# The DO API tier (Python backend) authenticates via the existing
# OIDC federation set up in `iam-azure-federation.tf` — Doppler ships
# a short-lived token that AWS STS exchanges for the per-feature role
# below. The Lambda workers assume the same roles via their execution
# role + `sts:AssumeRole` (declared inline per role).

data "aws_caller_identity" "native_features" {
  provider = aws.us_east_1
}

locals {
  native_features = {
    bedrock_cohere   = "Cohere embed + rerank via Amazon Bedrock"
    polly            = "Amazon Polly Neural / Generative TTS (3rd-tier voice)"
    transcribe       = "Amazon Transcribe STT (3rd-tier voice)"
    textract         = "Amazon Textract structured-document OCR path"
    rekognition      = "Amazon Rekognition image moderation pre-R2"
    comprehend       = "Amazon Comprehend sampled PII + sentiment"
    translate        = "Amazon Translate Indic<->EN fallback (after Sarvam)"
    personalize      = "Amazon Personalize home rail recommendations"
    fraud_detector   = "Amazon Fraud Detector signup + payment risk"
  }

  # Trust policy: Lambda workers + Doppler-federated DO backend.
  native_assume_role_doc = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      },
      {
        Effect    = "Allow"
        Principal = {
          Federated = "arn:aws:iam::${data.aws_caller_identity.native_features.account_id}:oidc-provider/token.actions.githubusercontent.com"
        }
        Action    = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = "repo:syrabit/syrabit:*"
          }
        }
      },
    ]
  })
}

resource "aws_iam_role" "native_feature" {
  for_each = local.native_features
  provider = aws.us_east_1

  name        = "${local.lz_project}-aws-native-${replace(each.key, "_", "-")}-${local.lz_env}"
  description = each.value

  assume_role_policy = local.native_assume_role_doc

  tags = merge(local.lz_common_tags, {
    Name    = "${local.lz_project}-aws-native-${each.key}"
    feature = each.key
  })
}

# ─── Per-feature inline policies (least privilege) ──────────────────

resource "aws_iam_role_policy" "bedrock_cohere" {
  provider = aws.us_east_1
  name     = "bedrock-cohere-embed-rerank"
  role     = aws_iam_role.native_feature["bedrock_cohere"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      # Cohere-only — Anthropic / Llama / Titan / Nova are intentionally
      # absent. See cloud-allocation-plan.md §9.
      Resource = [
        "arn:aws:bedrock:${local.lz_secondary_region}::foundation-model/cohere.embed-multilingual-v3",
        "arn:aws:bedrock:${local.lz_secondary_region}::foundation-model/cohere.rerank-v3-5:0",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "polly" {
  provider = aws.us_east_1
  name     = "polly-synthesize"
  role     = aws_iam_role.native_feature["polly"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["polly:SynthesizeSpeech", "polly:DescribeVoices", "polly:StartSpeechSynthesisTask"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "transcribe" {
  provider = aws.us_east_1
  name     = "transcribe-streaming"
  role     = aws_iam_role.native_feature["transcribe"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "transcribe:StartStreamTranscription",
          "transcribe:StartTranscriptionJob",
          "transcribe:GetTranscriptionJob",
        ]
        Resource = "*"
      },
      {
        # Task #337 — the voice.py 3rd-tier STT path stages the audio
        # clip in the transient ``syrabit-transcribe-tmp`` bucket
        # before calling StartTranscriptionJob; Transcribe then reads
        # the staged object via ``MediaFileUri``. Without these S3
        # actions the call fails at IAM, which is why the original
        # IAM-only-Transcribe scoping was rejected by code review.
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
        Resource = "arn:aws:s3:::${local.lz_project}-transcribe-tmp/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:aws:s3:::${local.lz_project}-transcribe-tmp"
      },
    ]
  })
}

# Task #337 — transient staging bucket for the Transcribe sync path.
# Lifecycle expires staged clips after 24h so we never accumulate
# private audio at rest beyond the runbook retention window.
resource "aws_s3_bucket" "transcribe_tmp" {
  provider = aws.us_east_1
  bucket   = "${local.lz_project}-transcribe-tmp"

  tags = merge(local.lz_common_tags, {
    Name    = "${local.lz_project}-transcribe-tmp"
    feature = "transcribe"
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "transcribe_tmp" {
  provider = aws.us_east_1
  bucket   = aws_s3_bucket.transcribe_tmp.id

  rule {
    id     = "expire-staged-audio-24h"
    status = "Enabled"

    expiration {
      days = 1
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_public_access_block" "transcribe_tmp" {
  provider                = aws.us_east_1
  bucket                  = aws_s3_bucket.transcribe_tmp.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_role_policy" "textract" {
  provider = aws.us_east_1
  name     = "textract-structured"
  role     = aws_iam_role.native_feature["textract"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "textract:AnalyzeDocument",
          "textract:DetectDocumentText",
          "textract:StartDocumentAnalysis",
          "textract:GetDocumentAnalysis",
        ]
        Resource = "*"
      },
      {
        # Textract reads uploads staged in the OCR-input bucket only.
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${local.lz_project}-ocr-input/*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "rekognition" {
  provider = aws.us_east_1
  name     = "rekognition-moderation"
  role     = aws_iam_role.native_feature["rekognition"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "rekognition:DetectModerationLabels",
        "rekognition:DetectFaces",
        "rekognition:DetectText",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "comprehend" {
  provider = aws.us_east_1
  name     = "comprehend-pii-sentiment"
  role     = aws_iam_role.native_feature["comprehend"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "comprehend:DetectPiiEntities",
        "comprehend:DetectSentiment",
        "comprehend:BatchDetectPiiEntities",
        "comprehend:BatchDetectSentiment",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "translate" {
  provider = aws.us_east_1
  name     = "translate-fallback"
  role     = aws_iam_role.native_feature["translate"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["translate:TranslateText", "translate:TranslateDocument"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "personalize" {
  provider = aws.us_east_1
  name     = "personalize-recommend"
  role     = aws_iam_role.native_feature["personalize"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "personalize:GetRecommendations",
          "personalize:GetPersonalizedRanking",
          "personalize-events:PutEvents",
          "personalize-events:PutUsers",
          "personalize-events:PutItems",
        ]
        Resource = "*"
      },
      {
        # Bulk import job reads click-history exports from S3.
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::${local.lz_project}-personalize-import",
          "arn:aws:s3:::${local.lz_project}-personalize-import/*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy" "fraud_detector" {
  provider = aws.us_east_1
  name     = "fraud-detector-score"
  role     = aws_iam_role.native_feature["fraud_detector"].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "frauddetector:GetEventPrediction",
        "frauddetector:GetDetectors",
      ]
      Resource = "*"
    }]
  })
}

# ─── Secrets Manager entries for callers that need static config ────
# These are *non-credential* config blobs (Personalize campaign ARN,
# Fraud Detector detector name, Bedrock guardrail ID) — kept in
# Secrets Manager so rotation is uniform with the rest of the worker
# secrets.

resource "aws_secretsmanager_secret" "native_feature_config" {
  for_each = local.native_features
  provider = aws.us_east_1

  name        = "${local.lz_project}/${local.lz_env}/aws-native/${replace(each.key, "_", "-")}"
  description = "Runtime config blob for ${each.value}"

  recovery_window_in_days = 7

  tags = merge(local.lz_common_tags, {
    Name    = "${local.lz_project}-aws-native-${each.key}-config"
    feature = each.key
  })
}

resource "aws_secretsmanager_secret_version" "native_feature_config_placeholder" {
  for_each      = aws_secretsmanager_secret.native_feature_config
  provider      = aws.us_east_1
  secret_id     = each.value.id
  secret_string = jsonencode({ _placeholder = "set-via-runbook-step-2" })

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# ─── Per-feature CloudWatch dashboards (admin panel reads from here) ─
# A single composite dashboard per feature keeps the admin AdminAwsNativePanel
# JSON contract stable: it can fetch the rendered widget JSON from the
# `Get-MetricWidgetImage` API for cost + latency at a glance.

resource "aws_cloudwatch_dashboard" "native_features" {
  provider       = aws.us_east_1
  dashboard_name = "${local.lz_project}-aws-native-${local.lz_env}"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Bedrock-Cohere invocations + p95 latency"
          region = local.lz_secondary_region
          metrics = [
            ["AWS/Bedrock", "Invocations", "ModelId", "cohere.embed-multilingual-v3"],
            [".", ".", ".", "cohere.rerank-v3-5:0"],
            [".", "InvocationLatency", "ModelId", "cohere.embed-multilingual-v3", { stat = "p95" }],
          ]
          period = 60
          stat   = "Sum"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Polly + Transcribe characters / seconds"
          region = local.lz_primary_region
          metrics = [
            ["AWS/Polly", "RequestCharacters"],
            ["AWS/Transcribe", "AudioSecondsProcessed"],
          ]
          period = 300
          stat   = "Sum"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Rekognition flagged uploads (moderation)"
          region = local.lz_primary_region
          metrics = [
            ["Syrabit/Moderation", "RekognitionFlaggedCount"],
            [".", "RekognitionScannedCount"],
          ]
          period = 300
          stat   = "Sum"
        }
      },
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          title  = "Personalize CTR vs deterministic fallback"
          region = local.lz_primary_region
          metrics = [
            ["Syrabit/Recommendations", "PersonalizeCtr"],
            [".", "DeterministicFallbackCtr"],
          ]
          period = 3600
          stat   = "Average"
        }
      },
    ]
  })
}

# ─── Outputs (consumed by admin billing panel + runbook) ────────────

output "aws_native_feature_role_arns" {
  description = "Per-feature IAM role ARN map; the admin billing panel keys cost reports off these."
  value       = { for k, r in aws_iam_role.native_feature : k => r.arn }
}

output "aws_native_feature_secret_arns" {
  description = "Per-feature Secrets Manager config blob ARN map."
  value       = { for k, s in aws_secretsmanager_secret.native_feature_config : k => s.arn }
}

output "aws_native_dashboard_url" {
  description = "Direct link to the AWS-native features CloudWatch dashboard."
  value = "https://${local.lz_secondary_region}.console.aws.amazon.com/cloudwatch/home?region=${local.lz_secondary_region}#dashboards:name=${aws_cloudwatch_dashboard.native_features.dashboard_name}"
}
