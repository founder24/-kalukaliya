# infra/aws/ecr.tf
#
# Phase 1b — AWS landing zone (Task #328).
#
# ECR repository for worker container images. One repo per worker keeps
# IAM scoping clean (deploy role above is scoped to `syrabit/*`).
#
# Lifecycle policy: keep the last 20 images, expire untagged images
# after 7 days. This keeps the repo cheap (Activate covers it but ECR
# storage still ticks) while preserving enough history to roll back.

locals {
  lz_worker_repos = [
    "email-worker",       # SES send fallback (already deployed; image lives here)
    "bedrock-proxy",      # Bedrock inference proxy (already deployed)
    "queue-fanout",       # Phase 4: Cloud Tasks → SQS consumer template
    "health-prober",      # Phase 4: per-replica health snapshot worker
  ]
}

resource "aws_ecr_repository" "workers" {
  for_each = toset(local.lz_worker_repos)

  name                 = "${local.lz_project}/${each.value}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.lz_common_tags, {
    Name   = "${local.lz_project}/${each.value}"
    worker = each.value
  })
}

resource "aws_ecr_lifecycle_policy" "workers" {
  for_each   = aws_ecr_repository.workers
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the last 20 tagged images"
        selection = {
          tagStatus     = "tagged"
          tagPatternList = ["*"]
          countType     = "imageCountMoreThan"
          countNumber   = 20
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
    ]
  })
}

# ─── bedrock-proxy repo in us-east-1 (secondary region) ─────────────────────
#
# The bedrock-proxy Lambda runs in us-east-1 because that is where
# cohere.embed-multilingual-v3 (Bedrock) is enabled.  Lambda can only
# pull images from ECR in the same region, so this repo must live in
# us-east-1 independently of the primary-region for_each above.
#
# The aws_iam_role_policy.github_deploy resource (iam-github-oidc.tf)
# grants ecr:CreateRepository on us-east-1 syrabit/* so the bootstrap
# workflow can create this repo before the first Docker push.

resource "aws_ecr_repository" "bedrock_proxy_use1" {
  provider             = aws.us_east_1
  name                 = "${local.lz_project}/bedrock-proxy"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = merge(local.lz_common_tags, {
    Name   = "${local.lz_project}/bedrock-proxy"
    worker = "bedrock-proxy"
  })
}

resource "aws_ecr_lifecycle_policy" "bedrock_proxy_use1" {
  provider   = aws.us_east_1
  repository = aws_ecr_repository.bedrock_proxy_use1.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the last 20 tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 20
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
    ]
  })
}

output "ecr_repository_urls" {
  value = merge(
    { for k, v in aws_ecr_repository.workers : k => v.repository_url },
    { "bedrock-proxy-use1" = aws_ecr_repository.bedrock_proxy_use1.repository_url },
  )
  description = "Push URLs for each worker image; consumed by aws-deploy-workers.yml."
}
