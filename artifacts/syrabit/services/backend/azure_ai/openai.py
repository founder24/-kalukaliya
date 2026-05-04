"""Azure OpenAI wrapper — additional LLM target for the AI Gateway.

Registered in the gateway routing table as ``llm/azure-openai``
(see ``artifacts/syrabit-backend/ai_gateway/registry.py``). Sits
parallel to direct OpenAI, Bedrock-Cohere, Groq, and Gemini —
*not* a replacement for any of them. The hosting plan reserves
GPT-4.1-mini chat/content roles for Azure OpenAI; Bedrock stays
Cohere-only.

The deployment names below are created out-of-band in the Azure
OpenAI Studio (Terraform's ``azurerm_cognitive_deployment`` is
intentionally not used — capacity quota requests are still
manual at Microsoft's end). Each deployment maps to a model the
gateway can route to under throttle.

Failure mode: 429 / 5xx is surfaced to the gateway as a
``ProviderThrottled`` exception so the existing failover ladder
moves to the next tier without retry-storming Azure quotas. The
admin health panel reads ``azure_openai_throttle`` from
``/api/admin/llm/health`` (see ``AdminAzureAiPanel.jsx``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Optional

from . import _resolver

# Deployment names match what is provisioned in Azure OpenAI Studio.
# Keep in sync with the gateway routing table; a CI check enforces
# the 1:1 mapping.
DEPLOYMENTS = {
    "gpt-4o":            "syra-gpt-4o",
    "gpt-4o-mini":       "syra-gpt-4o-mini",
    "gpt-4.1-mini":      "syra-gpt-41-mini",  # primary chat/content role per hosting plan
    "o4-mini":           "syra-o4-mini",
}

API_VERSION = "2024-10-21"  # GA preview that exposes structured outputs + streaming


@dataclass
class AzureOpenAIResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class ProviderThrottled(RuntimeError):
    """Raised on 429 so the gateway moves to the next failover tier."""


def _client():
    """Build a per-call AsyncAzureOpenAI client.

    Cheap to construct; uses the cached ``DefaultAzureCredential``
    so the token-fetch round-trip happens once per process.
    """
    from openai import AzureOpenAI
    from azure.identity import get_bearer_token_provider

    token_provider = get_bearer_token_provider(
        _resolver.get_credential(),
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureOpenAI(
        azure_endpoint=_resolver.endpoint_for("openai"),
        azure_ad_token_provider=token_provider,
        api_version=API_VERSION,
    )


def chat(
    messages: Iterable[dict],
    *,
    model: str = "gpt-4.1-mini",
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
) -> AzureOpenAIResponse:
    deployment = DEPLOYMENTS.get(model)
    if deployment is None:
        raise ValueError(
            f"Unknown model {model!r}; expected one of {sorted(DEPLOYMENTS)}"
        )

    started = time.monotonic()
    try:
        result = _client().chat.completions.create(
            model=deployment,
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # narrow once openai SDK exposes a typed 429
        if "429" in str(exc) or "Throttle" in str(exc):
            raise ProviderThrottled(str(exc)) from exc
        raise

    latency_ms = int((time.monotonic() - started) * 1000)
    choice = result.choices[0]
    usage = result.usage
    return AzureOpenAIResponse(
        text=choice.message.content or "",
        model=model,
        prompt_tokens=getattr(usage, "prompt_tokens", 0),
        completion_tokens=getattr(usage, "completion_tokens", 0),
        latency_ms=latency_ms,
    )
