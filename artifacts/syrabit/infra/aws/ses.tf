# infra/aws/ses.tf
#
# Phase 1b — AWS landing zone (Task #328).
#
# SES domain identity for `syrabit.ai`. The existing
# `lambda-email-worker.tf` registers a single mailbox identity
# (`no-reply@syrabit.ai`); the landing zone adds the *domain* identity
# so every `*@syrabit.ai` From-address can be used (alerts@, billing@,
# study-reminders@) without per-mailbox verification.
#
# Sandbox exit: `aws sesv2 put-account-details` is run out of band by
# the runbook to file the production-access request. Terraform does not
# represent the request because AWS doesn't expose it as a resource.

resource "aws_sesv2_email_identity" "syrabit_ai" {
  email_identity = "syrabit.ai"

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }

  configuration_set_name = aws_sesv2_configuration_set.workers.configuration_set_name

  tags = merge(local.lz_common_tags, {
    Name = "syrabit.ai"
  })
}

resource "aws_sesv2_email_identity_mail_from_attributes" "syrabit_ai" {
  email_identity         = aws_sesv2_email_identity.syrabit_ai.email_identity
  mail_from_domain       = "mail.syrabit.ai"
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

# Configuration set with TLS required, reputation metrics, and engagement
# tracking via SES Virtual Deliverability Manager.
resource "aws_sesv2_configuration_set" "workers" {
  configuration_set_name = "${local.lz_project}-workers"

  delivery_options {
    tls_policy = "REQUIRE"
  }

  reputation_options {
    reputation_metrics_enabled = true
  }

  sending_options {
    sending_enabled = true
  }

  tracking_options {
    custom_redirect_domain = "click.syrabit.ai"
  }

  tags = local.lz_common_tags
}

# Bounce + complaint events flow into the existing SES → SNS topic
# defined in `lambda-email-worker.tf`, but for the *domain* identity we
# expose a separate topic so the per-mailbox events stay distinguishable.
resource "aws_sns_topic" "ses_domain_events" {
  name = "${local.lz_project}-ses-domain-events"
  tags = local.lz_common_tags
}

resource "aws_sesv2_configuration_set_event_destination" "sns" {
  configuration_set_name = aws_sesv2_configuration_set.workers.configuration_set_name
  event_destination_name = "sns-bounce-complaint"

  event_destination {
    enabled              = true
    matching_event_types = ["BOUNCE", "COMPLAINT", "DELIVERY", "REJECT"]

    sns_destination {
      topic_arn = aws_sns_topic.ses_domain_events.arn
    }
  }
}

# ─── DKIM CNAME outputs (added to Cloudflare DNS by the runbook) ─────────────
# Cloudflare zone is managed by hand for the apex; we surface the three
# DKIM tokens here so the runbook can paste them directly.

output "ses_dkim_cname_records" {
  value = [
    for token in aws_sesv2_email_identity.syrabit_ai.dkim_signing_attributes[0].tokens : {
      name  = "${token}._domainkey.syrabit.ai"
      type  = "CNAME"
      value = "${token}.dkim.amazonses.com"
    }
  ]
  description = "Add each of these as a CNAME on the syrabit.ai Cloudflare zone."
}

output "ses_mail_from_records" {
  value = [
    {
      name  = "mail.syrabit.ai"
      type  = "MX"
      value = "10 feedback-smtp.${local.lz_primary_region}.amazonses.com"
    },
    {
      name  = "mail.syrabit.ai"
      type  = "TXT"
      value = "v=spf1 include:amazonses.com ~all"
    },
  ]
  description = "Custom MAIL FROM records for mail.syrabit.ai; add to Cloudflare DNS."
}

output "ses_domain_identity_arn" {
  value       = aws_sesv2_email_identity.syrabit_ai.arn
  description = "Used by worker runtime IAM policy (see iam-github-oidc.tf)."
}
