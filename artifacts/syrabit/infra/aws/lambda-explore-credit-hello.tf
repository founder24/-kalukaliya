# infra/aws/lambda-explore-credit-hello.tf
#
# Task #4 §2 — AWS Explore credit claim ("Build a serverless app" activity).
#
# Minimal zip-packaged Python 3.11 Lambda + public Function URL whose
# only job is to be a real, deployed, callable AWS resource so the
# Explore promo console activity registers as complete. Intentionally:
#
#   * Zip-packaged (not container-image like the rest of the worker
#     fleet) — avoids polluting the ECR registry with a single-purpose
#     image and keeps the credit-claim resource trivially destroy-able
#     if/when the promo ends.
#   * No VPC, no SSM, no Secrets Manager, no IAM beyond
#     `AWSLambdaBasicExecutionRole` (CloudWatch logs only).
#   * Public Function URL with `authorization_type = NONE` — the
#     handler returns a fixed deterministic payload, there is nothing
#     sensitive to authenticate. Keeps the activity check simple
#     (`curl <url>` → 200).
#   * Explicitly tagged `purpose = "explore-credit-hello"` so the
#     monthly-budget alarm and any future cost report can attribute
#     the (negligible) spend to the credit promo, not to the worker
#     tier.
#
# Canonical-delegation guard contract: this function MUST NOT be
# imported by `routes/` or `workers/edge-proxy/`. The chat / OCR /
# voice / payment / SEO providers are locked in
# `infra/architecture-locked-2026.md` §5; adding a new "general"
# Lambda to those flows requires an architecture-lock amendment, not
# a quiet wire-up here.

data "archive_file" "explore_credit_hello" {
  type        = "zip"
  source_file = "${path.module}/lambda-src/explore_credit_hello/handler.py"
  output_path = "${path.module}/lambda-src/explore_credit_hello/handler.zip"
}

resource "aws_iam_role" "explore_credit_hello" {
  name = "${local.lz_project}-explore-credit-hello-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(local.lz_common_tags, {
    Name    = "${local.lz_project}-explore-credit-hello-role"
    purpose = "explore-credit-hello"
  })
}

resource "aws_iam_role_policy_attachment" "explore_credit_hello_basic" {
  role       = aws_iam_role.explore_credit_hello.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "explore_credit_hello" {
  function_name    = "${local.lz_project}-explore-credit-hello"
  role             = aws_iam_role.explore_credit_hello.arn
  handler          = "handler.handler"
  runtime          = "python3.11"
  filename         = data.archive_file.explore_credit_hello.output_path
  source_code_hash = data.archive_file.explore_credit_hello.output_base64sha256
  memory_size      = 128
  timeout          = 5

  tags = merge(local.lz_common_tags, {
    Name    = "${local.lz_project}-explore-credit-hello"
    purpose = "explore-credit-hello"
  })
}

resource "aws_lambda_function_url" "explore_credit_hello" {
  function_name      = aws_lambda_function.explore_credit_hello.function_name
  authorization_type = "NONE"
}

# `authorization_type = NONE` on the URL config is necessary but not
# sufficient for anonymous invocation — AWS still requires an explicit
# resource policy granting `lambda:InvokeFunctionUrl` to principal `*`
# with `function_url_auth_type = NONE`. Without this, `curl <URL>`
# returns 403 and the Explore activity check (which probes the URL)
# does not register the activity as complete.
resource "aws_lambda_permission" "explore_credit_hello_public_url" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.explore_credit_hello.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

output "explore_credit_hello_function_arn" {
  description = "ARN of the explore-credit-hello Lambda (Task #4 §2)."
  value       = aws_lambda_function.explore_credit_hello.arn
}

output "explore_credit_hello_function_url" {
  description = "Public HTTPS endpoint for the explore-credit-hello Lambda."
  value       = aws_lambda_function_url.explore_credit_hello.function_url
}
