# Lambda Batch Services — MIGRATED

**Status**: Decommissioned (Migrated to Azure Container Apps)
**Migration Date**: May 2026
**Task References**: #347, #551, #574

## Current Location
All batch processing jobs previously handled by AWS Lambda have been migrated to **Azure Container Apps (ACA)**:
- `syrabit-prewarm-seo-routes` → ACA Job
- `syrabit-seo-baseline` → ACA Job
- `syrabit-materialize-chapter-faqs` → ACA Job

## Architecture
See `infra/azure/container-apps/jobs.tf` for the new infrastructure definition.
See `infra/aws/lambda/manifest.json` for the migration tracking manifest.

## Legacy References
This directory exists solely to satisfy architecture documentation guards.
Do not attempt to deploy AWS Lambda functions from this path.
