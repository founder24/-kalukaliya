"""Task #297 — pytest wrapper around scripts/check_dead_providers.py.

Task #347 update: AWS Bedrock has been fully decommissioned. The previous
Task #304 invariant (Bedrock default model pinned to ``amazon.nova-lite-v1:0``)
is replaced by an inverse invariant — Bedrock must NOT be reachable from
any active routing path or model registry.

Re-running locally:

    pytest artifacts/syrabit-backend/tests/test_dead_providers_guard.py -q
"""
import importlib
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


def test_task_347_bedrock_is_fully_decommissioned():
    """Task #347: Bedrock must be gone from every active routing surface.

    Asserts:
      1. ``providers/bedrock.py`` no longer exists (the previous import-safe
         stub was deleted as part of the hard-removal sweep).
      2. ``llm._PROVIDER_DEFAULT_MODELS`` does NOT contain a ``bedrock`` key.
      3. ``llm._PROVIDER_CANONICAL`` does NOT contain a ``bedrock`` key.
      4. ``config.PROVIDER_PRIORITY`` does not list ``bedrock`` for any
         feature pool.
    """
    backend_root = Path(__file__).resolve().parents[1]
    bedrock_module_path = backend_root / "providers" / "bedrock.py"
    assert not bedrock_module_path.exists(), (
        f"providers/bedrock.py must be deleted (Task #347), "
        f"but the file still exists at {bedrock_module_path}"
    )

    sys.path.insert(0, str(backend_root))
    try:
        # Force fresh imports — these modules may have been loaded earlier
        # in the test session before Task #347 deletions landed.
        for name in ("llm", "config"):
            if name in sys.modules:
                importlib.reload(sys.modules[name])
        from llm import _PROVIDER_DEFAULT_MODELS, _PROVIDER_CANONICAL
        from config import PROVIDER_PRIORITY
    finally:
        if str(backend_root) in sys.path:
            sys.path.remove(str(backend_root))

    assert "bedrock" not in _PROVIDER_DEFAULT_MODELS, (
        "llm._PROVIDER_DEFAULT_MODELS still contains 'bedrock' (Task #347 — must be removed)"
    )
    assert "bedrock" not in _PROVIDER_CANONICAL, (
        "llm._PROVIDER_CANONICAL still contains 'bedrock' (Task #347 — must be removed)"
    )
    for feature, pool in PROVIDER_PRIORITY.items():
        assert "bedrock" not in pool, (
            f"config.PROVIDER_PRIORITY[{feature!r}] still lists 'bedrock' "
            f"(Task #347 — every reference must be removed)"
        )
