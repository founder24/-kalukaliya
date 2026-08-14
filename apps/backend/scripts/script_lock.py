"""
scripts/script_lock.py
======================
Cross-process advisory lock so only one AHSEC ingestion / seed script
runs at a time inside the Replit container.

Uses a non-blocking exclusive flock on /tmp/syrabit_ingest.lock.
If the lock is already held by another script, the caller exits immediately
with a clear message — no waiting, no queuing, no resource contention.

Usage in any ingestion script:
    from scripts.script_lock import acquire_script_lock

    lock_fh = acquire_script_lock("ahsec_ingest")
    if lock_fh is None:
        sys.exit(0)   # already printed a message
    # do work — lock is released automatically when the process exits
"""
from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path

LOCK_FILE = Path("/tmp/syrabit_ingest.lock")
PID_FILE  = Path("/tmp/syrabit_ingest.pid")


def acquire_script_lock(script_name: str = "script") -> object | None:
    """
    Try to acquire an exclusive, non-blocking lock.

    Returns the open file-handle on success (caller must keep it alive for the
    lifetime of the process so the lock is not released prematurely).
    Returns None if another ingestion script is already running.
    """
    fh = LOCK_FILE.open("a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Read the PID of whoever holds the lock for a helpful message
        holder_pid = "unknown"
        try:
            holder_pid = PID_FILE.read_text().strip()
        except Exception:
            pass
        print(
            f"\n[script_lock] Another ingestion script is already running "
            f"(PID {holder_pid}). "
            f"Only one ingestion script may run at a time to avoid:\n"
            f"  • MongoDB connection pool exhaustion\n"
            f"  • fcntl file-lock deadlocks on the progress JSONL\n"
            f"  • CPU/RAM starvation in the Replit container\n\n"
            f"Wait for the other script to finish, then re-run {script_name}.\n",
            file=sys.stderr,
        )
        fh.close()
        return None

    # Write our PID so other scripts can report who holds the lock
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass

    return fh  # keep alive — GC would close the fd and release the lock
