"""Endpoint + credential resolution for Azure AI wrappers.

Endpoints are written to Key Vault by ``infra/azure/ai-services.tf``
as ``azure-ai-<feature>-endpoint`` non-sensitive secrets (the URL is
public information; the API key is intentionally never written).
This module memoises the lookup so each Container Apps Job /
backend container resolves once per process.

Auth uses ``DefaultAzureCredential`` so the same code path works in
three environments:

* Container Apps Jobs — assumes the user-assigned managed identity
  attached by ``infra/azure/container-apps-jobs.tf``.
* Local development — falls back to ``az login`` device code.
* CI smoke tests — falls back to the GitHub OIDC federated SP from
  ``infra/azure/iam-github-oidc.tf``.

No static API keys are ever read; every Cognitive Services account
in this landing zone runs with ``local_auth_enabled = false`` so a
leaked key cannot be used.
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from typing import Optional

# These imports are deferred so unit tests can import this module
# without the full Azure SDK installed in the test image.
_credential_lock = threading.Lock()
_credential_singleton = None

KEY_VAULT_URI_ENV = "AZURE_CRON_OBS_KV_URI"
"""Set by ``container-apps-jobs.tf`` per-job env block; for the
backend container it comes from the DO App Platform env via
``infra/dogcp/``."""


def get_credential():
    """Return a singleton ``DefaultAzureCredential``.

    Held behind a lock so concurrent first-call workers don't race
    the IMDS handshake.
    """
    global _credential_singleton
    if _credential_singleton is None:
        with _credential_lock:
            if _credential_singleton is None:
                from azure.identity import DefaultAzureCredential

                _credential_singleton = DefaultAzureCredential(
                    exclude_interactive_browser_credential=True,
                    # The managed-identity client ID is supplied via env
                    # so the same code resolves the cron-tier identity
                    # in ACA Jobs and the backend identity on DO without
                    # branching.
                    managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"),
                )
    return _credential_singleton


@lru_cache(maxsize=32)
def endpoint_for(feature: str) -> str:
    """Resolve the endpoint URL for an Azure AI feature from Key Vault.

    ``feature`` matches the keys in ``local.ai_services`` in
    ``infra/azure/ai-services.tf`` (e.g. ``"openai"``, ``"speech"``).
    """
    vault_uri = os.environ.get(KEY_VAULT_URI_ENV)
    if not vault_uri:
        # Loud failure — silent fallbacks to a hard-coded endpoint
        # would silently bypass the Key Vault rotation flow.
        raise RuntimeError(
            f"{KEY_VAULT_URI_ENV} not set; cannot resolve azure-ai-{feature}-endpoint"
        )

    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=vault_uri, credential=get_credential())
    secret = client.get_secret(f"azure-ai-{feature}-endpoint")
    if not secret.value:
        raise RuntimeError(f"azure-ai-{feature}-endpoint resolved to empty value")
    return secret.value


def reset_for_tests() -> None:
    """Clear cached credentials + endpoints between unit tests."""
    global _credential_singleton
    _credential_singleton = None
    endpoint_for.cache_clear()
