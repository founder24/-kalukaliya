"""Deprecated compatibility shim.

New code must import ``workers_ai_client``.  The old import path deliberately
contains no Sarvam API implementation or credentials, allowing older plugins
and tests to transition without reintroducing an external provider.
"""

from app.services.ai.workers_ai_client import (
    WorkersAIClient,
    generate_with_workers_ai,
    workers_ai_client,
)

SarvamAIClient = WorkersAIClient
sarvam_client = workers_ai_client


async def generate_with_sarvam(*args, **kwargs):
    """Backward-compatible alias that is served by Cloudflare Workers AI."""
    return await generate_with_workers_ai(*args, **kwargs)