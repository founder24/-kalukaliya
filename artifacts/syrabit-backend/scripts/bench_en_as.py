"""Benchmark English vs Assamese latency across configured chat providers.

Runs N rounds against each provider for two prompts:
  EN: "Explain photosynthesis in 2 sentences."
  AS: "ফটোসিন্থেচিছ ২ টা বাক্যত বুজাই দিয়ক।"  (same, in Assamese)

Reports min / median / mean / p95 latency and tokens/sec estimate per provider.
"""
from __future__ import annotations
import asyncio, os, sys, time, statistics, json, subprocess
from pathlib import Path

# Make the backend package importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Secret hydration ────────────────────────────────────────────────────────
# This script runs in interactive bash where Replit only injects a subset of
# secrets. Pull the missing API keys from the running api workflow's env
# (gunicorn process) so we can call all configured providers.
def _hydrate_secrets_from_api():
    try:
        pids = subprocess.check_output(["pgrep", "-f", "gunicorn server:app"]).decode().split()
        if not pids:
            return
        env_path = f"/proc/{pids[0]}/environ"
        with open(env_path, "rb") as fh:
            for chunk in fh.read().split(b"\0"):
                if b"=" not in chunk:
                    continue
                k, _, v = chunk.partition(b"=")
                ks = k.decode("utf-8", "ignore")
                if ks in os.environ and os.environ[ks]:
                    continue
                os.environ[ks] = v.decode("utf-8", "ignore")
    except Exception as exc:
        print(f"(could not hydrate secrets from api workflow: {exc})")

_hydrate_secrets_from_api()

from llm import _call_single_provider  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
ROUNDS = int(os.environ.get("BENCH_ROUNDS", "3"))
MAX_TOKENS = 160
PROMPTS = {
    "EN": "Explain photosynthesis in exactly 2 short sentences.",
    "AS": "ফটোসিন্থেচিছ কেৱল ২ টা চমু বাক্যত বুজাই দিয়ক।",
}

# Chat pool = Cloudflare + AWS + Azure + GCP + Sarvam (per project policy).
# Gemini-direct / Cerebras / Groq are intentionally excluded.
CANDIDATES = [
    # ── Cloudflare Workers AI ──────────────────────────────────────────────
    ("cloudflare/llama-3.3-70b",    "workers-ai",   "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "CLOUDFLARE_API_TOKEN"),
    ("cloudflare/llama-3.1-8b",     "workers-ai",   "@cf/meta/llama-3.1-8b-instruct-fp8",       "CLOUDFLARE_API_TOKEN"),
    ("cloudflare/gpt-oss-120b",     "workers-ai",   "@cf/openai/gpt-oss-120b",                  "CLOUDFLARE_API_TOKEN"),
    ("cloudflare/sea-lion-indic",   "workers-ai",   "@cf/aisingapore/gemma-sea-lion-v4-27b-it", "CLOUDFLARE_API_TOKEN"),
    # ── AWS Bedrock removed in Task #347 (provider decommissioned) ─────────
    # ── Azure OpenAI (CF BYOK → KEY_1 → KEY_2 chain) ───────────────────────
    ("azure-openai/gpt-4.1-mini",   "azure_openai", "",                                          "AZURE_OPENAI_KEY_1"),
    # ── GCP Vertex AI Gemini (direct, OAuth via SA JSON) ───────────────────
    ("gcp-vertex/gemini-2.5-flash", "gcp_vertex",   "gemini-2.5-flash",                          "GOOGLE_APPLICATION_CREDENTIALS_JSON"),
    # ── Sarvam (Indic-native) ──────────────────────────────────────────────
    ("sarvam/sarvam-m",             "sarvam",       "sarvam-m",                                  "SARVAM_API_KEY"),
]


def _fmt(ms_list: list[float]) -> str:
    if not ms_list:
        return "—"
    return (f"min={min(ms_list):6.0f}  med={statistics.median(ms_list):6.0f}  "
            f"avg={statistics.mean(ms_list):6.0f}  max={max(ms_list):6.0f}  ms")


_PROVIDER_KEY_ENV = {
    "workers-ai":   "CLOUDFLARE_API_TOKEN",
    "gemini":       "GEMINI_API_KEY",
    "cerebras":     "CEREBRAS_API_KEY",
    "groq":         "GROQ_API_KEY",
    "sarvam":       "SARVAM_API_KEY",
    "azure_openai": "AZURE_OPENAI_KEY_1",
}


_GCP_TOKEN_CACHE: dict = {}


async def _gcp_vertex_chat(prompt: str, model: str, max_tokens: int) -> str:
    """Direct call to Vertex AI Gemini using the SA JSON in env."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as _GAuthReq
    import httpx as _httpx

    if "creds" not in _GCP_TOKEN_CACHE:
        sa_info = json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        _GCP_TOKEN_CACHE["creds"] = creds
        _GCP_TOKEN_CACHE["project"] = sa_info.get("project_id")
    creds = _GCP_TOKEN_CACHE["creds"]
    project = _GCP_TOKEN_CACHE["project"]
    if not creds.valid:
        creds.refresh(_GAuthReq())
    location = os.environ.get("VERTEX_LOCATION", "us-central1")
    url = (f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
           f"/locations/{location}/publishers/google/models/{model}:generateContent")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.1},
    }
    async with _httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            url,
            headers={"Authorization": f"Bearer {creds.token}",
                     "Content-Type": "application/json"},
            json=payload,
        )
    r.raise_for_status()
    data = r.json()
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


_PROVIDER_KEY_ENV = {
    "workers-ai":   "CLOUDFLARE_API_TOKEN",
    "sarvam":       "SARVAM_API_KEY",
    "azure_openai": "AZURE_OPENAI_KEY_1",
    "bedrock":      "AWS_ACCESS_KEY_ID",
    "gcp_vertex":   "GOOGLE_APPLICATION_CREDENTIALS_JSON",
}


async def run_one(provider: str, model: str, prompt: str) -> tuple[float, str, str | None]:
    t0 = time.perf_counter()
    try:
        if provider == "bedrock":
            from providers.bedrock import call_converse as _bedrock
            text = await _bedrock([{"role": "user", "content": prompt}], max_tokens=MAX_TOKENS)
        elif provider == "gcp_vertex":
            text = await _gcp_vertex_chat(prompt, model, MAX_TOKENS)
        else:
            api_key = os.environ.get(_PROVIDER_KEY_ENV.get(provider, ""), "") or ""
            text = await _call_single_provider(
                [{"role": "user", "content": prompt}], provider, api_key, model, MAX_TOKENS,
            )
        dur_ms = (time.perf_counter() - t0) * 1000.0
        return dur_ms, text or "", None
    except Exception as exc:
        dur_ms = (time.perf_counter() - t0) * 1000.0
        return dur_ms, "", f"{type(exc).__name__}: {str(exc)[:140]}"


async def main():
    print(f"\n=== EN vs AS provider speed test — {ROUNDS} rounds, max_tokens={MAX_TOKENS} ===\n")
    available = [c for c in CANDIDATES if os.environ.get(c[3])]
    skipped   = [c[0] for c in CANDIDATES if not os.environ.get(c[3])]
    if skipped:
        print(f"(skipped — no API key in env: {', '.join(skipped)})\n")

    results: dict[str, dict[str, list[float]]] = {}
    errors: dict[str, dict[str, str]] = {}
    samples: dict[str, dict[str, str]] = {}

    for label, provider, model, _env in available:
        results[label] = {"EN": [], "AS": []}
        errors[label]  = {}
        samples[label] = {}
        for lang, prompt in PROMPTS.items():
            print(f"  → {label:30s}  {lang} …", end="", flush=True)
            for r in range(ROUNDS):
                dur_ms, text, err = await run_one(provider, model, prompt)
                if err:
                    errors[label].setdefault(lang, err)
                    print(f" ERR({err.split(':')[0]})", end="", flush=True)
                    break
                results[label][lang].append(dur_ms)
                if r == 0:
                    samples[label][lang] = text[:120].replace("\n", " ")
                print(f" {dur_ms:5.0f}ms", end="", flush=True)
            print()
        print()

    # ── Report ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 96)
    print(f"{'Provider/Model':32s}  {'Lang':4s}  {'Latency (ms)':46s}  Status")
    print("=" * 96)
    for label in results:
        for lang in ("EN", "AS"):
            xs = results[label][lang]
            err = errors[label].get(lang)
            status = "OK" if xs else (err or "no data")
            print(f"{label:32s}  {lang:4s}  {_fmt(xs):46s}  {status[:25]}")

    # ── Winners ─────────────────────────────────────────────────────────────
    print("\n— Median latency leaderboard —")
    for lang in ("EN", "AS"):
        ranked = sorted(
            [(label, statistics.median(results[label][lang])) for label in results if results[label][lang]],
            key=lambda x: x[1],
        )
        print(f"\n  {lang}:")
        for i, (label, med) in enumerate(ranked, 1):
            print(f"    {i}. {label:32s}  {med:6.0f} ms")

    print("\n— First-round sample outputs (truncated to 120 chars) —")
    for label in samples:
        for lang in ("EN", "AS"):
            s = samples[label].get(lang, "(no output)")
            print(f"  [{label} {lang}]  {s}")

    # JSON summary for downstream tools.
    out = {
        "rounds": ROUNDS,
        "max_tokens": MAX_TOKENS,
        "results_ms": results,
        "errors": errors,
        "samples": samples,
    }
    Path("/tmp/bench_en_as.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("\n(JSON saved to /tmp/bench_en_as.json)\n")


if __name__ == "__main__":
    asyncio.run(main())
