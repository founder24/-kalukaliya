# AWS Explore credit claim — runbook (Task #4)

**Goal:** unlock the full $100 of "Explore AWS" promo credits by completing
all 5 activities, then leave only the two **KEEP** resources behind
(monthly budget + hello-Lambda). The other three (EC2, Bedrock playground,
RDS/Aurora) are **claim-and-destroy** — they exist only long enough for
the Explore dashboard to mark the activity complete, then we tear them
down so they cannot bill against the credit pool.

This task is **console-driven** because the Explore promo only credits
activities that are completed via the AWS console UI; calling the same
APIs from CLI/Terraform does not register the claim. The Terraform side
(budget + hello-Lambda) lives in `artifacts/syrabit/infra/aws/` and is
already prepped — you only need to `terraform apply` after activity #2.

## Pre-flight

1. Confirm the agent has written:
   - `artifacts/syrabit/infra/aws/account-billing.tf` — budget at 60 / 80 / 95 % wired to `ops_alerts` SNS (mirrors `cost_caps` ladder).
   - `artifacts/syrabit/infra/aws/lambda-explore-credit-hello.tf` + `lambda-src/explore_credit_hello/handler.py` — zip-packaged Python 3.11 Lambda with a public Function URL.
2. Open the AWS console signed in as the same account that holds the worker tier (the one whose ID is in `_root.tf`).
3. Open https://aws.amazon.com/awstc/ → **Explore AWS** dashboard. Confirm "0 / 5 activities complete, $0 / $100 credits earned".

## Activities

### 1. Set up a budget — KEEP

1. Console → **AWS Budgets** → **Create a budget** → choose **Cost budget** → **Monthly** → **$100**.
2. Add three actual-cost notifications at **60 %, 80 %, 95 %** + one forecasted at 80 %.
3. **Save** so the Explore dashboard ticks the activity, then **delete the console-created budget** and run `bash workflows/tf-apply` (the `tf-apply` workflow). The Terraform-managed `syrabit-prod-monthly` budget replaces it with the same thresholds + SNS topic wiring.
4. Verify in console: `syrabit-prod-monthly` shows **4 alerts** and the SNS topic `syrabit-ops-alerts` is in the recipient list.

### 2. Build a serverless app (Lambda + Function URL) — KEEP

1. Run the `tf-apply` workflow. Wait for `aws_lambda_function.explore_credit_hello` and `aws_lambda_function_url.explore_credit_hello` to finish.
2. Copy the two Terraform outputs:
   - `explore_credit_hello_function_arn` →  fill into `aws-landing-zone.md` §11 placeholder `<ARN>`.
   - `explore_credit_hello_function_url` → fill into the `<URL>` placeholder.
3. `curl <URL>` from any laptop and confirm `{"ok": true, "service": "syrabit", "purpose": "explore-credit-hello", "wired_into_production": false}`.
4. Console → **Lambda → syrabit-explore-credit-hello → Test**. Run the default test event once so the Explore activity check sees a successful invocation.

### 3. Launch an EC2 instance — CLAIM + DELETE

1. Console → **EC2 → Launch instance**. Name `syrabit-explore-throwaway`. AMI: Amazon Linux 2023 (free-tier eligible). Type: **`t2.micro`**. Key pair: **proceed without** (you will not SSH). Network: default VPC, public subnet. Storage: leave at 8 GiB gp3 default.
2. Launch. Wait until state = **Running** AND status checks = **2/2 passed**. The Explore dashboard polls status, not just launch.
3. **Immediately** Actions → **Terminate instance**. Wait until state = **Terminated**.
4. Console → **EBS → Volumes**: confirm the auto-attached root volume is also **deleted** (delete-on-termination is the default but check). Delete any leftover.

### 4. Bedrock playground — CLAIM ONLY (do NOT wire)

1. Console → **Amazon Bedrock** (region `us-east-1`) → **Model access** → request access to **Anthropic Claude 3 Haiku** (smallest model, instant approval) and **Amazon Titan Text Lite**. Wait for status **Access granted**.
2. **Playgrounds → Chat → Claude 3 Haiku → "Hello"** → run once. The Explore activity ticks on the first successful invocation.
3. **DO NOT** add Bedrock to `_select_chat_model` / `_select_assamese_model` / `content_formatter` / `voice/`. The canonical-delegation table in `infra/architecture-locked-2026.md` §5.1 lists Vertex / Sarvam / Workers AI as the only approved chat providers; `scripts/check_canonical_delegation.py` (when added in a future task) will fail CI on any Bedrock import.
4. Leave model access **enabled** — Bedrock has no per-model fixed cost; you only pay per token, and we are not invoking it.

### 5. Aurora / RDS smallest cluster — CLAIM + DELETE

1. Console → **RDS → Create database** → **Standard create** → **Aurora (PostgreSQL Compatible)** → **Aurora Standard** → engine version: latest available LTS.
2. Templates → **Dev/Test**. DB cluster id: `syrabit-explore-throwaway`. Master username: `admin`. **Auto-generate password** (you will not connect). Instance class: **`db.t3.medium`** (smallest Aurora-supported). Single AZ, single instance. **Disable** Performance Insights, Enhanced Monitoring, automated backups (set retention to **0 days** so deletion is instant).
3. Create. Wait until cluster state = **Available** (~10 min). The Explore activity ticks at this point.
4. **Immediately** Actions → **Delete** on the instance, then on the cluster. Tick **Skip final snapshot** + **acknowledge data loss**.
5. Wait until both instance and cluster disappear from the list. Console → **RDS → Snapshots → Manual / Automated**: confirm zero snapshots tagged `syrabit-explore-throwaway`.

## Post-flight verification

Run all of these from the AWS console (CLI optional):

| Check | Expected | Where |
| --- | --- | --- |
| Explore dashboard | **5 / 5 activities complete, $100 / $100 credits** | https://aws.amazon.com/awstc/ |
| Credits balance | `+$100 promotional, expires <date>` | Billing → Credits |
| EC2 instances filtered by `tag:Name=syrabit-explore-throwaway` | **none** | EC2 → Instances |
| EBS volumes filtered by `tag:Name=syrabit-explore-throwaway` | **none** | EC2 → Volumes |
| RDS clusters | **no** `syrabit-explore-throwaway` cluster or instance | RDS → Databases |
| RDS snapshots | **no** snapshot tagged `syrabit-explore-throwaway` | RDS → Snapshots |
| Lambda functions | `syrabit-explore-credit-hello` **present**, all other `syrabit-explore-*` **absent** | Lambda → Functions |
| Cost Explorer (last 24 h, filtered by `purpose=explore-credit-hello` and untagged Explore activities) | **$0.00 actual spend** (or $0.00 net after credit) | Billing → Cost Explorer |

Once all rows pass, paste the Function ARN + Function URL into the
two placeholders in `aws-landing-zone.md` §11 and remove the
`<FILL_IN_AFTER_APPLY>` markers.

## Rollback

- Budget: `terraform destroy -target=aws_budgets_budget.monthly_cost` then re-create via console (one-shot manual).
- Hello-Lambda: `terraform destroy -target=aws_lambda_function_url.explore_credit_hello -target=aws_lambda_function.explore_credit_hello -target=aws_iam_role_policy_attachment.explore_credit_hello_basic -target=aws_iam_role.explore_credit_hello`. The credits do not get clawed back by AWS even if the originating resource is removed.
