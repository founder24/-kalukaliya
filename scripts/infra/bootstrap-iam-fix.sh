#!/usr/bin/env bash
# One-shot script — run locally with admin AWS credentials.
# Fixes the syrabit-github-deploy role so the lambda-bootstrap
# GitHub Actions workflow can run via OIDC.
#
# Requirements: aws CLI configured with an IAM user/role that has
#   iam:UpdateAssumeRolePolicy + iam:PutRolePolicy permissions.
#
# Usage:
#   chmod +x scripts/infra/bootstrap-iam-fix.sh
#   ./scripts/infra/bootstrap-iam-fix.sh
#
set -euo pipefail

ROLE="syrabit-github-deploy"
ACCOUNT="271740379017"

echo "=== Step 1: Fix OIDC trust policy ==="
aws iam update-assume-role-policy \
  --role-name "$ROLE" \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "GitHubActionsOIDC",
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::271740379017:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": [
            "repo:founder24/-kalukaliya:ref:refs/heads/master",
            "repo:founder24/-kalukaliya:ref:refs/heads/main",
            "repo:founder24/-kalukaliya:ref:refs/heads/release/*",
            "repo:founder24/-kalukaliya:environment:prod",
            "repo:founder24/-kalukaliya:environment:staging"
          ]
        }
      }
    }]
  }'

echo "✓ Trust policy updated (repo: founder24/-kalukaliya)"

echo ""
echo "=== Step 2: Replace deploy-perms inline policy ==="
aws iam put-role-policy \
  --role-name "$ROLE" \
  --policy-name "deploy-perms" \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "EcrAuth",
        "Effect": "Allow",
        "Action": ["ecr:GetAuthorizationToken"],
        "Resource": ["*"]
      },
      {
        "Sid": "EcrPush",
        "Effect": "Allow",
        "Action": [
          "ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
          "ecr:BatchGetImage", "ecr:DescribeRepositories",
          "ecr:DescribeImages", "ecr:ListImages"
        ],
        "Resource": [
          "arn:aws:ecr:ap-south-1:271740379017:repository/syrabit/*",
          "arn:aws:ecr:us-east-1:271740379017:repository/syrabit/*"
        ]
      },
      {
        "Sid": "LambdaDeploy",
        "Effect": "Allow",
        "Action": [
          "lambda:GetFunction", "lambda:ListFunctions",
          "lambda:CreateFunction", "lambda:DeleteFunction",
          "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration",
          "lambda:PublishVersion", "lambda:UpdateAlias", "lambda:GetAlias",
          "lambda:CreateAlias", "lambda:DeleteAlias", "lambda:ListVersionsByFunction",
          "lambda:AddPermission", "lambda:RemovePermission", "lambda:GetPolicy",
          "lambda:TagResource", "lambda:UntagResource", "lambda:ListTags"
        ],
        "Resource": [
          "arn:aws:lambda:ap-south-1:271740379017:function:syrabit-*",
          "arn:aws:lambda:us-east-1:271740379017:function:syrabit-*"
        ]
      },
      {
        "Sid": "LambdaESM",
        "Effect": "Allow",
        "Action": [
          "lambda:CreateEventSourceMapping", "lambda:DeleteEventSourceMapping",
          "lambda:UpdateEventSourceMapping", "lambda:GetEventSourceMapping",
          "lambda:ListEventSourceMappings"
        ],
        "Resource": ["*"]
      },
      {
        "Sid": "SchedulerDeploy",
        "Effect": "Allow",
        "Action": [
          "scheduler:CreateSchedule", "scheduler:UpdateSchedule",
          "scheduler:DeleteSchedule", "scheduler:GetSchedule",
          "scheduler:ListSchedules", "scheduler:TagResource", "scheduler:UntagResource"
        ],
        "Resource": [
          "arn:aws:scheduler:ap-south-1:271740379017:schedule/default/syrabit-*"
        ]
      },
      {
        "Sid": "LogGroupCreate",
        "Effect": "Allow",
        "Action": [
          "logs:CreateLogGroup", "logs:PutRetentionPolicy", "logs:DeleteLogGroup",
          "logs:DescribeLogGroups", "logs:DescribeLogStreams",
          "logs:GetLogEvents", "logs:FilterLogEvents"
        ],
        "Resource": [
          "arn:aws:logs:ap-south-1:271740379017:log-group:/aws/lambda/syrabit-*",
          "arn:aws:logs:ap-south-1:271740379017:log-group:/aws/lambda/syrabit-*:*",
          "arn:aws:logs:us-east-1:271740379017:log-group:/aws/lambda/syrabit-*",
          "arn:aws:logs:us-east-1:271740379017:log-group:/aws/lambda/syrabit-*:*",
          "arn:aws:logs:ap-south-1:271740379017:log-group:/syrabit/workers",
          "arn:aws:logs:ap-south-1:271740379017:log-group:/syrabit/workers:*"
        ]
      },
      {
        "Sid": "PassRuntimeRole",
        "Effect": "Allow",
        "Action": ["iam:PassRole"],
        "Resource": [
          "arn:aws:iam::271740379017:role/syrabit-workers-runtime"
        ],
        "Condition": {
          "StringEquals": {
            "iam:PassedToService": ["lambda.amazonaws.com", "scheduler.amazonaws.com"]
          }
        }
      }
    ]
  }'

echo "✓ deploy-perms policy updated (CreateFunction + scheduler:* added)"
echo ""
echo "Both steps complete. Go to GitHub Actions → lambda-bootstrap → Run workflow."
