"""Task #297 — pytest wrapper around scripts/check_dead_providers.py.

Runs the guard script and asserts it exits 0. Re-running locally:

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
