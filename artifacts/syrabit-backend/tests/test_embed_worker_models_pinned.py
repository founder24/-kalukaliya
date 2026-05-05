"""Task #382 — Pin the worker's default model set to embedding endpoints.

Code review rejected the first cut because the worker pointed at chat /
instruct models (``@cf/google/gemma-3-1b-it`` / ``@cf/qwen/qwen2.5-0.5b-
instruct``) which do not return embeddings under
``env.AI.run({ text: [...] })``. This regression test reads the worker
source directly and asserts:

  * ``DEFAULT_MODELS`` references dedicated embedding endpoints
    (``-embedding-`` or ``embeddinggemma`` in the slug).
  * The chat-model slugs that broke the previous contract are NOT in
    the default list.
  * The fusion path L2-normalises each per-model vector before
    summation (so different native widths can be combined safely).

The Cloudflare Workers runtime can't be exercised from the Python
suite, but pinning the source-level contract here makes a re-regression
fail loudly during local test runs without needing a wrangler dev
process.
"""
from __future__ import annotations

import os
import re

WORKER_SRC = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "syrabit", "workers", "embed-worker", "src", "index.ts",
)


def _read_source() -> str:
    with open(WORKER_SRC, encoding="utf-8") as fh:
        return fh.read()


def test_default_models_are_dedicated_embedding_endpoints():
    src = _read_source()
    match = re.search(r'DEFAULT_MODELS\s*=\s*\n?\s*"([^"]+)"', src)
    assert match, "DEFAULT_MODELS literal not found in worker source"
    models = [m.strip() for m in match.group(1).split(",") if m.strip()]
    assert models, "DEFAULT_MODELS resolved to empty list"
    for slug in models:
        assert ("embedding" in slug.lower()), (
            f"DEFAULT_MODELS slug {slug!r} is not a Workers-AI embedding "
            "endpoint — env.AI.run({text: [...]}) on chat/instruct models "
            "does not return embeddings"
        )


def test_default_models_no_longer_reference_chat_instruct_slugs():
    src = _read_source()
    # The two slugs from the rejected first cut.
    forbidden = ("@cf/google/gemma-3-1b-it", "@cf/qwen/qwen2.5-0.5b-instruct")
    # Allow them to appear inside a comment, but not inside the
    # DEFAULT_MODELS literal.
    default_block = re.search(
        r'DEFAULT_MODELS\s*=\s*\n?\s*"([^"]+)"', src,
    )
    assert default_block, "DEFAULT_MODELS literal not found in worker source"
    literal = default_block.group(1)
    for bad in forbidden:
        assert bad not in literal, (
            f"chat/instruct slug {bad!r} re-introduced into DEFAULT_MODELS — "
            "those models do not return embeddings via env.AI.run({text: [...]})"
        )


def test_fuse_embedding_l2_normalises_each_model_before_summation():
    """The per-model L2 normalisation step is what lets us safely sum
    vectors from models with different native scales (e.g. gemma-300m
    at 768-dim vs qwen3-embedding at 1024-dim) without one drowning out
    the other. Pin its presence so a future refactor doesn't regress
    fused-vector quality silently."""
    src = _read_source()
    fuse_block_match = re.search(
        r"async function fuseEmbedding\([^{]*\)\s*:\s*Promise<number\[\]>\s*\{[\s\S]+?\n\}",
        src,
    )
    assert fuse_block_match, "fuseEmbedding function body not found"
    fuse_body = fuse_block_match.group(0)
    # We expect a per-model normalisation INSIDE the loop. Also ensure
    # the resize-to-common-dims step happens before summation so unequal
    # native widths can be fused.
    assert "l2Normalise(perModel)" in fuse_body, (
        "fuseEmbedding must L2-normalise each per-model vector before summation"
    )
    assert "resizeToDims" in fuse_body, (
        "fuseEmbedding must resize per-model vectors to the common output width"
    )


def test_run_model_embedding_handles_canonical_workers_ai_shape():
    """Workers-AI embedding endpoints (bge-m3, embeddinggemma,
    qwen3-embedding) return ``{ shape: [N, D], data: [[...]] }`` —
    each row is a vector. Make sure the worker reads that shape first
    so a future re-order doesn't break the primary path."""
    src = _read_source()
    fn_match = re.search(
        r"async function runModelEmbedding\([^{]*\)\s*:\s*Promise<number\[\]>\s*\{[\s\S]+?\n\}",
        src,
    )
    assert fn_match, "runModelEmbedding function body not found"
    body = fn_match.group(0)
    # Shape 1 (data: [[...]]) must be the FIRST branch checked,
    # because it is the canonical embeddings response shape.
    shape_arr_idx = body.find("Array.isArray(result.data[0])")
    shape_obj_idx = body.find("result.data[0]?.embedding")
    assert 0 <= shape_arr_idx < shape_obj_idx, (
        "runModelEmbedding must check the array-of-arrays embedding "
        "shape before the OpenAI-compatible {data: [{embedding}]} shape"
    )
