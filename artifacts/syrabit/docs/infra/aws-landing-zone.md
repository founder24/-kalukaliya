# AWS Landing Zone Runbook — Async Workers, Queues & SES

**Status:** Live (Phase 1b of ADR-0001)
**Owner:** infra
**Task:** #328
**Companion:** [`ADR-0001-four-way-hosting-rebalance.md`](ADR-0001-four-way-hosting-rebalance.md), [`provider-credit-matrix.md`](provider-credit-matrix.md)
**Terraform root:** [`../../infra/aws/`](../../infra/aws/)

---

## 1. What this account hosts

This AWS account is **only** the foundation for the Phase 4 worker tier:

- Async workers (Lambda + occasional Fargate) that consume SQS queues
  ported from GCP Cloud Tasks.
- Durable fan-out queues (SQS + DLQ).
- Transactional email *fallback* tier (SES; Resend is still primary).
- Bedrock inference proxy (already deployed; lives in `us-east-1`).

It is **not** the home of:

- The synchronous API tier (FastAPI) or the Rust core — both go to
  Azure Container Apps.
- Cron / scheduled jobs — those land on Azure Container Apps Jobs.
- ElastiCache / Redis — Upstash stays as the Redis surface.
- Production DNS — Cloudflare keeps the apex and `api.syrabit.ai`.

## 2. Account & regions

| Item                         | Value                                                  |
|------------------------------|--------------------------------------------------------|
| AWS account ID               | _populated post-apply; see `terraform output`_         |
| Account alias                | `syrabit-prod`                                         |
| Billing contact              | `ops@syrabit.ai`                                       |
| Activate credits             | $1 000 (applied 2026-04; balance tracked in [`provider-credit-matrix.md`](provider-credit-matrix.md)) |
| Primary region               | `ap-south-1` (Mumbai — closest to majority of users)  |
| Secondary / DR region        | `us-east-1` (already hosts the Bedrock proxy)          |
| Monthly cost budget          | $100 (50 % actual / 80 % forecast → email alert)       |
| Cost anomaly threshold       | $25 absolute daily impact → email alert                |

The DR region is documented but not actively replicated to today.
CloudWatch logs are mirrored across via the
`workers_dr_mirror` log group so a regional CloudWatch outage still
leaves a queryable copy.

## 3. Network baseline (`network.tf`)

```
VPC syrabit-workers-vpc       10.40.0.0/16   (ap-south-1)
├── public-a   10.40.0.0/20   ap-south-1a    (NAT lives here)
├── public-b   10.40.16.0/20  ap-south-1b
├── private-a  10.40.32.0/20  ap-south-1a    (workers)
└── private-b  10.40.48.0/20  ap-south-1b    (workers)
```

- Single NAT in AZ-a (cost-tuned; the worker tier tolerates a brief
  egress hiccup; DR is the secondary region, not a multi-NAT setup).
- Interface VPC endpoints for **SQS, Secrets Manager, SES SMTP** plus a
  gateway endpoint for **S3** — keeps Lambda → AWS API calls off NAT.
- Security groups:
  - `syrabit-workers-egress` — outbound 443 to anywhere + DNS (UDP/TCP
    53) to the VPC resolver, no inbound. Attach to every worker Lambda
    / Fargate task. (DNS egress is required so private-subnet ENIs can
    resolve the interface VPC endpoints below via private DNS names.)
  - `syrabit-vpc-endpoints` — 443 inbound from `workers-egress`.

Outputs (consumed by Phase 4 Terraform):
`workers_vpc_id`, `workers_private_subnet_ids`, `workers_security_group_id`.

## 4. Identity (`iam-github-oidc.tf`)

Three things live here, deliberately split:

1. **GitHub OIDC provider** — trust for `token.actions.githubusercontent.com`.
2. **`syrabit-github-deploy` role** — assumed by the
   `aws-deploy-workers.yml` workflow. Restricted to:
   - `repo:syrabit/syrabit:ref:refs/heads/master`
   - `repo:syrabit/syrabit:ref:refs/heads/release/*`
   - `repo:syrabit/syrabit:environment:{prod,staging}`
   Permissions: ECR push, Lambda update, `iam:PassRole` on the runtime
   role only, CloudWatch log read.
3. **`syrabit-workers-runtime` role** — assumed by Lambda
   (`lambda.amazonaws.com`) and Fargate (`ecs-tasks.amazonaws.com`).
   Permissions: `GetSecretValue` on the worker secrets only,
   SQS receive/send on `syrabit-*`, SES send via the verified domain
   identity, `PutMetricData` on `Syrabit/Workers` only.

A compromised CI runner cannot read application secrets; a compromised
worker cannot redeploy itself.

### GitHub setup steps

```bash
# After `terraform apply`, copy the role ARN into the repo's GH env.
gh secret set AWS_ROLE_ARN \
  --env prod \
  --body "$(terraform output -raw github_deploy_role_arn)"
gh variable set AWS_REGION --env prod --body ap-south-1
```

The workflow then assumes the role with:

```yaml
permissions:
  id-token: write
  contents: read
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
    aws-region: ${{ vars.AWS_REGION }}
```

## 5. Container registry (`ecr.tf`)

One repository per worker, `image_tag_mutability = IMMUTABLE`,
`scan_on_push = true`, AES256 encrypted.

| Logical name    | Repository                   | Used by                                |
|-----------------|------------------------------|----------------------------------------|
| `email-worker`  | `syrabit/email-worker`       | `lambda-email-worker.tf` (deployed)    |
| `bedrock-proxy` | `syrabit/bedrock-proxy`      | `lambda-bedrock-proxy.tf` (deployed)   |
| `queue-fanout`  | `syrabit/queue-fanout`       | Phase 4 SQS consumer template          |
| `health-prober` | `syrabit/health-prober`      | Phase 4 per-replica probe worker       |

Lifecycle policy: keep last 20 tagged images; expire untagged > 7 d.

## 6. Secrets (`secrets.tf`)

All secrets live under `syrabit/prod/<group>/<name>` in AWS Secrets
Manager, with `recovery_window_in_days = 7`. Plaintext values are
populated **out of band** from 1Password — Terraform only declares the
container and a `_placeholder` initial value. `lifecycle.ignore_changes`
on `secret_string` means rotations don't drift state.

| Secret name                          | Env var in workers           | Source of truth          |
|--------------------------------------|------------------------------|--------------------------|
| `supabase/service-role-key`          | `SUPABASE_SERVICE_ROLE_KEY`  | 1Password `Supabase`     |
| `upstash/redis-rest-token`           | `UPSTASH_REDIS_REST_TOKEN`   | 1Password `Upstash`      |
| `resend/api-key`                     | `RESEND_API_KEY`             | 1Password `Resend`       |
| `stripe/webhook-secret`              | `STRIPE_WEBHOOK_SECRET`      | 1Password `Stripe`       |
| `razorpay/webhook-secret`            | `RAZORPAY_WEBHOOK_SECRET`    | 1Password `Razorpay`     |
| `sentry/dsn-workers`                 | `SENTRY_DSN`                 | 1Password `Sentry`       |
| `axiom/ingest-token`                 | `AXIOM_INGEST_TOKEN`         | 1Password `Axiom`        |
| `slack/ops-webhook`                  | (SNS subscription; see §7)   | 1Password `Slack`        |
| `pinecone/api-key`                   | `PINECONE_API_KEY`           | 1Password `Pinecone`     |
| `cohere/api-key`                     | `COHERE_API_KEY`             | 1Password `Cohere`       |

ARNs are emitted by the `worker_secret_arns` Terraform output.

### Populating a secret

```bash
aws secretsmanager put-secret-value \
  --region ap-south-1 \
  --secret-id syrabit/prod/resend/api-key \
  --secret-string "$(op read 'op://syrabit/Resend/api-key')"
```

### Rotation

90-day reminder per secret (calendar invite owned by infra). Rotate via
the same `put-secret-value` command; workers pick up the new value on
the next invocation because they call `GetSecretValue` per cold start
and cache for the lifetime of the execution environment only.

If a secret is suspected leaked: rotate first, **then** revoke the old
version with `aws secretsmanager update-secret-version-stage`.

## 7. Observability (`observability.tf`)

- **CloudWatch log group** `/syrabit/workers` (30-day retention) for
  ad-hoc worker output not tied to an auto-created `/aws/lambda/*`
  group.
- **DR landing-pad log group** `/syrabit/workers-dr-landing` in
  `us-east-1` (14-day retention). Pre-created so a failed-over worker
  has a destination immediately; **not** an automatic cross-region
  replica. Cross-region forwarding (subscription → Kinesis → cross-
  region put) is deferred to the Phase 4 worker tier task.
- **Custom metric namespace** `Syrabit/Workers` — runtime role is
  scoped to this namespace only via an IAM `cloudwatch:namespace`
  condition.
- **SNS topic** `syrabit-ops-alerts` — every CloudWatch alarm in this
  account should set this as its alarm action. Topic policy already
  allows `cloudwatch.amazonaws.com`, `budgets.amazonaws.com`, and
  `costalerts.amazonaws.com` to publish.

### Subscribing Slack to the SNS topic

The Slack incoming webhook URL is a secret (lives in
`syrabit/prod/slack/ops-webhook`). We do **not** put the URL in
Terraform state. Subscribe out of band:

```bash
WEBHOOK=$(aws secretsmanager get-secret-value \
  --region ap-south-1 \
  --secret-id syrabit/prod/slack/ops-webhook \
  --query SecretString --output text | jq -r .url)

aws sns subscribe \
  --region ap-south-1 \
  --topic-arn "$(terraform output -raw ops_alerts_topic_arn)" \
  --protocol https \
  --notification-endpoint "$WEBHOOK"
```

Slack auto-confirms the subscription. Verify with a test publish:

```bash
aws sns publish \
  --region ap-south-1 \
  --topic-arn "$(terraform output -raw ops_alerts_topic_arn)" \
  --message "landing-zone smoke from $(whoami)@$(hostname)"
```

## 8. SES (`ses.tf`)

- Domain identity for **`syrabit.ai`** with EasyDKIM (RSA 2048).
- Custom MAIL FROM domain `mail.syrabit.ai` (improves SPF alignment).
- Configuration set `syrabit-workers`: TLS required, reputation metrics
  on, sending enabled, click tracking via `click.syrabit.ai`.
- Bounce / complaint / delivery / reject events publish to SNS topic
  `syrabit-ses-domain-events`.
- The mailbox identity `no-reply@syrabit.ai` continues to exist via
  `lambda-email-worker.tf`; the domain identity adds every other
  `*@syrabit.ai` From-address without per-mailbox verification.

### Cloudflare DNS records to add

After `terraform apply`, copy these into the syrabit.ai Cloudflare
zone (DNS-only / grey cloud):

```bash
terraform output -json ses_dkim_cname_records
terraform output -json ses_mail_from_records
```

There are 3 DKIM CNAMEs, 1 MAIL FROM MX, and 1 MAIL FROM SPF TXT.

### Production-access (sandbox exit) request

SES starts in sandbox mode (200 emails/day, verified recipients only).
Submit the production-access request:

```bash
aws sesv2 put-account-details \
  --region ap-south-1 \
  --mail-type TRANSACTIONAL \
  --website-url https://syrabit.ai \
  --use-case-description "Transactional fallback for Resend: OTP codes, password resets, payment receipts, weekly study digests. Bounce/complaint handling via SNS topic syrabit-ses-domain-events. Hard-bounce suppression on by default. Volume estimate: 5k/day steady-state, 50k/day peak." \
  --additional-contact-email-addresses ops@syrabit.ai \
  --production-access-enabled
```

Approval typically lands within 24 h. Track in the
`#infra-alerts` Slack channel.

## 9. Apply order

The files are independent at the resource level but a clean first apply
should be:

```bash
cd artifacts/syrabit/infra/aws
terraform init
terraform apply \
  -target=aws_secretsmanager_secret.workers \
  -target=aws_sesv2_email_identity.syrabit_ai \
  -target=aws_sesv2_configuration_set.workers
terraform apply
```

(The first targeted apply unblocks the IAM runtime policy that
references the secret ARNs and the SES identity ARN.)

## 10. Access

- **Console:** SSO via `syrabit.awsapps.com/start` →
  `AWSAdministratorAccess` (humans) or
  `AWSPowerUserAccess` (devs). Root is locked away in 1Password.
- **CLI:** `aws sso login --profile syrabit-prod`. Profile config:
  ```ini
  [profile syrabit-prod]
  sso_session = syrabit
  sso_account_id = <account-id>
  sso_role_name = AWSAdministratorAccess
  region = ap-south-1
  output = json
  ```
- **CI:** GitHub Actions only, via the OIDC role in §4. No
  long-lived AWS access keys exist in this account.

## 11. What's intentionally not here

| Thing                                | Lives where                                          |
|--------------------------------------|------------------------------------------------------|
| SQS queues, Lambda consumers         | Phase 4 Terraform (downstream task)                  |
| ElastiCache / Redis                  | Upstash (unchanged)                                  |
| API ingress, Rust core gRPC          | Azure Container Apps                                 |
| Cron jobs                            | Azure Container Apps Jobs                            |
| Bedrock / Polly / Transcribe IAM     | Phase "AWS-native advanced features" task            |
| Production DNS apex (`syrabit.ai`)   | Cloudflare; only `api.syrabit.ai` latency record     |
|                                      | lives in `route53-latency.tf` (and that's not a      |
|                                      | landing-zone concern)                                |

## 12. Decommission notes

If this landing zone ever has to be torn down:

1. Drain every SQS queue to empty; delete consumers first.
2. `terraform destroy` in reverse-dependency order (SES last because
   the Cloudflare DNS records anchor email reputation).
3. Cancel the production-access SES status with AWS support so the
   account doesn't leave a high-reputation IP unmanaged.
4. Revoke the GitHub OIDC role **before** deleting it (so any in-flight
   workflow run fails fast instead of silently using a stale role).
