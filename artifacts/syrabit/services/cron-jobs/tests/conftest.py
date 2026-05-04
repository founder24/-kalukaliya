"""Test bootstrap for cron-jobs runner (Task #332).

The Container Apps Job image installs `artifacts/syrabit-backend/`
onto the container's PYTHONPATH (see `services/cron-jobs/Dockerfile`).
Replicate that here so `import server`, `import routes.*`, and the
per-target backend modules resolve when the dispatch test imports
them.
"""

from __future__ import annotations

import pathlib
import sys


def _resolve_backend_dir() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor.parent / "syrabit-backend"
        if candidate.is_dir() and (candidate / "server.py").is_file():
            return candidate
    raise RuntimeError(
        f"Could not locate syrabit-backend dir from {here}"
    )


_BACKEND_DIR = _resolve_backend_dir()
_sp = str(_BACKEND_DIR)
if _sp not in sys.path:
    sys.path.insert(0, _sp)
