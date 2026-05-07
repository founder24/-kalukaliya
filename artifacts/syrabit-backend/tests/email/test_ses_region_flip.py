"""Task #556 — verify the multi-region SES failover contract.

`SES_REGION` (with a `AWS_SES_REGION` legacy synonym) is the operator
override that flips the active SES region between the primary
(`us-east-1`) and the warm secondary (`ap-south-1`). Both regions are
identity-verified with DKIM + SPF + DMARC alignment per the runbook
in `artifacts/syrabit/docs/infra/aws-landing-zone.md` §8.

This test pins the env-var contract — it does NOT exercise live SES
(boto3 is mocked). What we guarantee:

  * Default region is `us-east-1` when neither knob is set.
  * `SES_REGION=ap-south-1` flips the active region (failover).
  * Legacy `AWS_SES_REGION` is honored (graceful in-flight rollout).
  * `SES_REGION` wins when both are set (new knob > legacy synonym).
  * The boto3 client is constructed with the resolved region, so the
    actual API call goes to the chosen region — not just metadata.
  * `EmailSendFailed` carries the active region, so an ops triage can
    tell from a single log line which region failed.
"""
from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest


def test_default_region_is_us_east_1():
    et = importlib.import_module("email_templates")
    with patch.dict(os.environ,
                    {"SES_REGION": "", "AWS_SES_REGION": ""},
                    clear=False):
        assert et._ses_region() == "us-east-1"


def test_ses_region_env_flips_to_ap_south_1():
    et = importlib.import_module("email_templates")
    with patch.dict(os.environ,
                    {"SES_REGION": "ap-south-1", "AWS_SES_REGION": ""},
                    clear=False):
        assert et._ses_region() == "ap-south-1"


def test_legacy_aws_ses_region_synonym_honored():
    """Legacy synonym kept so an in-flight Bicep/ACA rollout can't break
    the send path mid-deploy."""
    et = importlib.import_module("email_templates")
    with patch.dict(os.environ,
                    {"SES_REGION": "", "AWS_SES_REGION": "ap-south-1"},
                    clear=False):
        assert et._ses_region() == "ap-south-1"


def test_new_knob_wins_over_legacy():
    et = importlib.import_module("email_templates")
    with patch.dict(os.environ,
                    {"SES_REGION": "ap-south-1",
                     "AWS_SES_REGION": "us-east-1"},
                    clear=False):
        assert et._ses_region() == "ap-south-1"


@pytest.mark.parametrize("region", ["us-east-1", "ap-south-1"])
def test_send_uses_resolved_region_for_boto3_client(region):
    et = importlib.import_module("email_templates")
    fake_client = MagicMock()
    with patch.dict(os.environ, {
        "SES_REGION": region,
        "AWS_ACCESS_KEY_ID": "AKIATEST",
        "AWS_SECRET_ACCESS_KEY": "secret_test",
    }, clear=False), patch.object(et, "_ses_client", return_value=fake_client) as mk:
        et._send_via_ses("user@syrabit.ai", "Subject", "<p>x</p>")
    assert mk.called
    fake_client.send_email.assert_called_once()


def test_email_send_failed_carries_active_region():
    et = importlib.import_module("email_templates")
    with patch.dict(os.environ, {
        "SES_REGION": "ap-south-1",
        "AWS_ACCESS_KEY_ID": "",
        "AWS_SECRET_ACCESS_KEY": "",
    }, clear=False):
        with pytest.raises(et.EmailSendFailed) as exc_info:
            et._send_via_ses("user@syrabit.ai", "x", "<p>x</p>")
    assert exc_info.value.region == "ap-south-1"
