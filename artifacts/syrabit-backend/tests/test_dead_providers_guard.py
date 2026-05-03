"""Task #297 — pytest wrapper around scripts/check_dead_providers.py.

Also pins Bedrock's first-class model identity so the deprecated
``nova-micro`` string cannot creep back into active routing/admin code
(Task #304).

Re-running locally:

    pytest artifacts/syrabit-backend/tests/test_dead_providers_guard.py -q
"""
import subprocess
import sys
from pathlib import Path


def test_no_banned_provider_tokens():
    script = Path(__file__).resolve().parents[1] / "scripts" / "check_dead_providers.py"
    assert script.exists(), f"missing CI guard script at {script}"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Dead-provider guard found violations:\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def test_task_304_bedrock_model_is_nova_lite_not_micro():
    """Task #304: Bedrock's default model must be Nova Lite (the all-in-one
    chat+vision model) — Nova Micro is text-only and was the previous default.

    Asserts:
      1. providers.bedrock._MODEL_ID  == 'amazon.nova-lite-v1:0'
      2. llm._PROVIDER_DEFAULT_MODELS['bedrock'] == 'amazon.nova-lite-v1:0'
      3. The deprecated 'amazon.nova-micro-v1:0' literal does not appear in
         any active routing / admin / provider source file (historical bench
         result snapshots in bench_results/ are excluded — they record what
         actually ran on a given date).
    """
    backend_root = Path(__file__).resolve().parents[1]

    sys.path.insert(0, str(backend_root))
    try:
        import providers.bedrock as _bk
        from llm import _PROVIDER_DEFAULT_MODELS
    finally:
        if str(backend_root) in sys.path:
            sys.path.remove(str(backend_root))

    assert _bk._MODEL_ID == "amazon.nova-lite-v1:0", (
        f"providers.bedrock._MODEL_ID must be amazon.nova-lite-v1:0, got {_bk._MODEL_ID!r}"
    )
    assert _PROVIDER_DEFAULT_MODELS.get("bedrock") == "amazon.nova-lite-v1:0", (
        f"llm._PROVIDER_DEFAULT_MODELS['bedrock'] must be amazon.nova-lite-v1:0, "
        f"got {_PROVIDER_DEFAULT_MODELS.get('bedrock')!r}"
    )

    # Scan active source paths for the deprecated literal. Historical bench
    # snapshots in bench_results/ are intentionally excluded — they record
    # what actually ran on a given date.
    scan_roots = [
        backend_root / "providers",
        backend_root / "routes",
        backend_root / "scripts",
        backend_root / "llm.py",
        backend_root / "config.py",
    ]
    offenders: list[str] = []
    for root in scan_roots:
        if root.is_file():
            files = [root]
        else:
            files = list(root.rglob("*.py"))
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            if "amazon.nova-micro-v1:0" in text:
                offenders.append(str(f.relative_to(backend_root)))
    assert not offenders, (
        f"Task #304: deprecated 'amazon.nova-micro-v1:0' literal found in "
        f"active source files (must be 'amazon.nova-lite-v1:0'): {offenders}"
    )
