# Task #audit — Terraform variable overrides for lambda-batch-jobs.tf.
#
# gcp_total_credits_usd: total GCP startup-credit pool size in USD.
# The chat-credit-runway Lambda computes:
#   remaining = gcp_total_credits_usd - cumulative_gcp_spend
# If this is 0 (the default), remaining is always <= 0 and the 80%
# degradation ladder fires immediately on every request.
#
# Set this to the actual credit amount shown in:
#   GCP Console → Billing → Credits → Total credits
# Common values: 300 (free tier), 1000 (startup program), 5000 (Google for Startups).
# Update and run `terraform apply` whenever the credit pool is refreshed.
#
# COST-CAP-OVERRIDE: raising this above 0 does NOT raise the $100/mo cap
# (enforced separately by cost_caps.py + check_budget_ceiling.py). It only
# gives the runway Lambda the correct baseline for its % calculation.
gcp_total_credits_usd = 300

# GCP billing export coordinates (match what's configured in BigQuery).
# These are the BigQuery dataset details for the billing export.
gcp_billing_project      = "blissful-acumen-495019-t6"
gcp_billing_dataset      = "billing_export"
gcp_billing_table_prefix = "gcp_billing_export_v1"
