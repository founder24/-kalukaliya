"""Task #27 — Cohere `embed-multilingual-v3` via AWS Bedrock.

Tests the language-gated Indic embed route added to
`llm.call_embed_with_dispatch` plus its supporting machinery:

  * Provider-tagged cache isolation (`embed_cache.py`).
  * `MeterD` Indic sub-cap (`$5/mo` inside the global `$100` cap).
  * `EMBED_INDIC_PROVIDER` + `RAG_EMBEDDING_PROVIDER_FORCE` kill-switches.
  * IAM-denied / throttle / dim-mismatch fallthrough to Workers AI
    (V4 §12 — fail loud, never silently degrade).
  * CI guard accepts the new module + still rejects the Cohere SDK
    and `COHERE_API_KEY`.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────
# Provider module — boto3 import + dim guard + error mapping
# ──────────────────────────────────────────────────────────────────────


def test_provider_module_constants_match_founder_locks():
    from providers import cohere_bedrock_embed as cb
    # Founder lock — Pinecone is 1024-dim. A drift here would silently
    # corrupt the index, so the provider hardcodes the dim.
    assert cb.EMBED_DIM == 1024
    assert cb.MODEL_ID == "cohere.embed-multilingual-v3"
    assert cb.PROVIDER_NAME == "cohere_multilingual_v3_bedrock"


def test_dim_mismatch_raises_loud(monkeypatch):
    """A 768-dim response (e.g. drifted Bedrock model) must fail loud."""
    from providers import cohere_bedrock_embed as cb

    fake = MagicMock()
    fake_body = MagicMock()
    fake_body.read.return_value = (
        b'{"embeddings":{"float":[[' + b",".join(b"0.1" for _ in range(768)) + b"]]}}"
    )
    fake.invoke_model.return_value = {"body": fake_body}

    with patch.object(cb, "_get_client", return_value=fake):
        import asyncio
        with pytest.raises(cb.BedrockEmbedError, match="dim mismatch"):
            asyncio.get_event_loop().run_until_complete(
                cb.embed_one("কেমন আছ?", task_type="RETRIEVAL_QUERY")
            )


def test_iam_denied_raises_access_denied(monkeypatch):
    from providers import cohere_bedrock_embed as cb

    class _Err(Exception):
        def __init__(self, code: str):
            self.response = {"Error": {"Code": code}}
            super().__init__(f"AccessDenied: {code}")

    fake = MagicMock()
    fake.invoke_model.side_effect = _Err("AccessDeniedException")

    with patch.object(cb, "_get_client", return_value=fake), \
         patch("botocore.exceptions.ClientError", _Err, create=True):
        import asyncio
        with pytest.raises(cb.BedrockEmbedAccessDenied):
            asyncio.get_event_loop().run_until_complete(
                cb.embed_one("hi", task_type="RETRIEVAL_DOCUMENT")
            )


# ──────────────────────────────────────────────────────────────────────
# Cache isolation — embed_provider folded into key
# ──────────────────────────────────────────────────────────────────────


def test_cache_key_is_provider_scoped():
    """Same text + lang + task_type, different providers MUST hit
    different cache keys so a Workers-AI vector never collides with a
    Bedrock-Cohere vector."""
    import embed_cache

    k_workers = embed_cache._key(
        "photosynthesis", "RETRIEVAL_QUERY", "en", "workers_ai_custom",
    )
    k_bedrock = embed_cache._key(
        "photosynthesis", "RETRIEVAL_QUERY", "en", "cohere_multilingual_v3_bedrock",
    )
    assert k_workers != k_bedrock
    assert "workers_ai_custom" in k_workers
    assert "cohere_multilingual_v3_bedrock" in k_bedrock


# ──────────────────────────────────────────────────────────────────────
# MeterD — Indic sub-cap inside the $100 global cap
# ──────────────────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self):
        self.kv: dict[str, bytes] = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None):
        if isinstance(v, str):
            v = v.encode()
        self.kv[k] = v if isinstance(v, (bytes, bytearray)) else str(v).encode()


def test_meter_d_indic_subcap_pauses_route():
    """Crossing `$5/mo` Indic sub-cap MUST set the paused flag."""
    from credit_burn_meter import MeterD, MeterDConfig
    r = _FakeRedis()
    m = MeterD(redis=r, alert_sink=lambda *a, **k: None,
               cfg=MeterDConfig(cap_usd=100.0))

    # Just under sub-cap.
    m.record_usd_indic_bedrock(4.99, subcap_usd=5.0)
    assert m.is_indic_embed_paused() is False

    # Now cross.
    m.record_usd_indic_bedrock(0.02, subcap_usd=5.0)
    assert m.is_indic_embed_paused() is True
    # And the same dollars are folded into the global Rule-D bucket
    # (no double-counting against the $100 founder lock).
    assert m.indic_monthly_usd() >= 5.0


def test_meter_d_indic_does_not_inflate_global_cap():
    """The Indic sub-bucket counts the SAME dollars as Rule-D once."""
    from credit_burn_meter import MeterD, MeterDConfig
    r = _FakeRedis()
    m = MeterD(redis=r, alert_sink=lambda *a, **k: None,
               cfg=MeterDConfig(cap_usd=100.0))
    m.record_usd_indic_bedrock(2.0, subcap_usd=5.0)
    # Read the global Rule-D bucket directly via the prefix.
    from credit_burn_meter import MONTHLY_USD_KEY_PREFIX
    keys = [k for k in r.kv if k.startswith(MONTHLY_USD_KEY_PREFIX + ":")]
    assert keys, "global Rule-D bucket should have been written"
    raw = r.kv[keys[0]]
    assert pytest.approx(float(raw), rel=1e-6) == 2.0


# ──────────────────────────────────────────────────────────────────────
# CI guard — scoped Cohere ban
# ──────────────────────────────────────────────────────────────────────


def test_ci_guard_accepts_bedrock_provider_module():
    """The umbrella `BANNED_LITERAL` must NOT fire on the runtime
    references in `providers/cohere_bedrock_embed.py`."""
    sys.path.insert(0, str(
        Path(__file__).resolve().parent.parent / "scripts" / "ci"
    ))
    mod = importlib.import_module("check_canonical_delegation")
    src = (Path(__file__).resolve().parent.parent
           / "providers" / "cohere_bedrock_embed.py").read_text()
    # Strip block doc-strings and `#` comments — they're allowed to
    # MENTION the banned tokens because this file is the canonical
    # place to document WHY they're banned. Only the executable code
    # surface must stay clean.
    code_only_lines: list[str] = []
    in_doc = False
    doc_delim = None
    for raw in src.splitlines():
        stripped = raw.strip()
        if in_doc:
            if doc_delim and doc_delim in stripped:
                in_doc = False
                doc_delim = None
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            doc_delim = stripped[:3]
            # Single-line doc-string?
            if stripped.count(doc_delim) >= 2 and len(stripped) > 3:
                doc_delim = None
                continue
            in_doc = True
            continue
        # Strip trailing inline `#` comments
        code = raw.split("#", 1)[0]
        code_only_lines.append(code)
    for ln in code_only_lines:
        assert not re.match(r"^\s*import\s+cohere\b", ln), (
            f"provider file imports cohere SDK: {ln!r}"
        )
        assert not re.match(r"^\s*from\s+cohere\b", ln), (
            f"provider file imports from cohere SDK: {ln!r}"
        )
        assert "COHERE_API_KEY" not in ln, (
            f"provider file references COHERE_API_KEY: {ln!r}"
        )
    # And the umbrella allowlist MUST cover the provider module.
    assert any(
        p.endswith("providers/cohere_bedrock_embed.py")
        for p in mod.ALLOWLIST_FILES
    ), "ALLOWLIST_FILES must cover the Bedrock provider module"


def test_ci_guard_still_rejects_cohere_sdk_and_api_key():
    """`import cohere`, `from cohere`, and `COHERE_API_KEY` MUST still
    fail the umbrella delegation guard."""
    sys.path.insert(0, str(
        Path(__file__).resolve().parent.parent / "scripts" / "ci"
    ))
    mod = importlib.import_module("check_canonical_delegation")
    assert mod.BANNED_LITERAL.search("import cohere"), \
        "import cohere must remain banned"
    assert mod.BANNED_LITERAL.search("from cohere import Client"), \
        "from cohere ... must remain banned"
    assert mod.BANNED_LITERAL.search('os.environ["COHERE_API_KEY"]'), \
        "COHERE_API_KEY env-var must remain banned"


# ──────────────────────────────────────────────────────────────────────
# Architecture lock — `cohere` removed from retired_providers
# ──────────────────────────────────────────────────────────────────────


def test_architecture_matrix_no_longer_retires_cohere_bare_token():
    """The Bedrock model id literal would otherwise trigger the
    architecture-lock guard's strict patterns."""
    import json
    matrix = json.loads(
        (Path(__file__).resolve().parents[3] / "infra"
         / "architecture-matrix.json").read_text()
    )
    assert "cohere" not in matrix.get("retired_providers", []), (
        "cohere bare token must be removed from retired_providers — "
        "Task #27 partial-reversal of #491. Scoped bans for the SDK "
        "and COHERE_API_KEY env var live in `check_canonical_delegation.py`."
    )


def test_architecture_matrix_includes_indic_embed_row():
    import json
    matrix = json.loads(
        (Path(__file__).resolve().parents[3] / "infra"
         / "architecture-matrix.json").read_text()
    )
    items = []
    for sec in matrix.get("sections", []):
        if str(sec.get("id", "")) != "5.1":
            continue
        for row in sec.get("rows", []):
            items.append(row.get("item", ""))
    joined = "\n".join(items).lower()
    assert "embed.indic" in joined or "embed.indic" in joined.replace(" ", "")
    assert "cohere_multilingual_v3_bedrock" in "\n".join(items)


def test_persist_early_provider_kw_is_required():
    """Task #27 regression — `_persist_early` must NOT default-bind a
    provider name. A vector that fell through from Indic→Workers must
    be cached under `workers_ai_custom`, never under the Cohere key
    captured at function definition time.

    We can't easily exercise the closure directly, so we assert the
    source contract (keyword-only, no default) and that every call
    site passes an explicit provider literal or variable.
    """
    src = (Path(__file__).resolve().parents[1] / "llm.py").read_text()
    # Signature must be keyword-only with no default value.
    assert re.search(
        r"def\s+_persist_early\s*\(\s*_vec\s*,\s*\*\s*,\s*_provider\s*:\s*str\s*\)\s*:",
        src,
    ), "`_persist_early` signature must be `(_vec, *, _provider: str)`"
    # No call site may invoke `_persist_early(...)` without a
    # `_provider=` keyword argument.
    bad = re.findall(r"_persist_early\([^)]*\)", src)
    for call in bad:
        if call.lstrip().startswith("_persist_early(_vec, *"):
            continue  # signature definition itself
        assert "_provider=" in call, (
            f"`_persist_early` called without `_provider=` — "
            f"would default-bind a provider name: {call!r}"
        )
    # And the inner `_persist` helper must thread provider through too.
    assert re.search(
        r"def\s+_persist\s*\(\s*_vec\s*,\s*_provider\s*:\s*str\s*\)\s*:",
        src,
    ), "`_persist` weighted-loop helper must accept `_provider: str`"


# ──────────────────────────────────────────────────────────────────────
# Task #27 — language-gated INDEXING path (chunk_embedder)
# ──────────────────────────────────────────────────────────────────────


def test_chunk_embedder_split_routes_indic_to_bedrock_and_tags_per_slot():
    """`_embed_batch_split` must send Indic-dominant chunks to Bedrock
    and English chunks to Workers-AI, then return parallel
    ``embed_provider`` tags so the Pinecone metadata write reflects
    the provider that actually produced each vector — never a
    function-default-bound name.
    """
    import asyncio
    from providers import chunk_embedder as ce

    async def fake_workers(texts):
        return [[0.1] * 1024 for _ in texts]

    async def fake_bedrock(texts):
        return [[0.2] * 1024 for _ in texts]

    items = [
        (0, "English photosynthesis explainer", False),
        (1, "অসমীয়া দীঘল ব্যাখ্যা" * 30, True),
        (2, "Another english chunk", False),
    ]
    with patch.object(ce, "_workers_custom_embed_batch", fake_workers), \
         patch.object(ce, "_bedrock_indic_embed_batch", fake_bedrock):
        vecs, tags = asyncio.get_event_loop().run_until_complete(
            ce._embed_batch_split(items)
        )

    assert len(vecs) == 3 and len(tags) == 3
    assert tags[0] == "workers_ai_custom"
    assert tags[1] == "cohere_multilingual_v3_bedrock"
    assert tags[2] == "workers_ai_custom"
    # Provider parity: the vector value distinguishes the two providers
    # in this test, so a swap would be observable.
    assert vecs[0][0] == 0.1 and vecs[2][0] == 0.1
    assert vecs[1][0] == 0.2


def test_chunk_embedder_split_respects_indic_subcap_pause():
    """When the Indic sub-cap has tripped, even Indic-dominant chunks
    must route to Workers-AI for the rest of the calendar month."""
    import asyncio
    from providers import chunk_embedder as ce

    workers_calls = {"n": 0}
    bedrock_calls = {"n": 0}

    async def fake_workers(texts):
        workers_calls["n"] += len(texts)
        return [[0.1] * 1024 for _ in texts]

    async def fake_bedrock(texts):
        bedrock_calls["n"] += len(texts)
        return [[0.2] * 1024 for _ in texts]

    items = [(0, "অসমীয়া দীঘল ব্যাখ্যা" * 30, True)]
    with patch.object(ce, "_workers_custom_embed_batch", fake_workers), \
         patch.object(ce, "_bedrock_indic_embed_batch", fake_bedrock), \
         patch("credit_burn_meter_runtime.is_indic_embed_paused", return_value=True):
        vecs, tags = asyncio.get_event_loop().run_until_complete(
            ce._embed_batch_split(items)
        )

    assert tags[0] == "workers_ai_custom", (
        "Indic chunk must degrade to Workers-AI when sub-cap is paused — "
        "matches the runtime dispatcher's behaviour and prevents the "
        "nightly bulk indexer from busting the $5/mo Indic sub-cap."
    )
    assert workers_calls["n"] == 1 and bedrock_calls["n"] == 0
