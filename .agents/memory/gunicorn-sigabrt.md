---
name: Gunicorn SIGABRT on Cloud Run
description: Gunicorn worker timeout=30s causes SIGABRT crashes on Cloud Run when AI requests exceed 30s; set timeout=120 for async AI workloads
---

## The Rule
`gunicorn_conf.py`: set `timeout = 120` (not 30) for this backend.

**Why:** AI requests (Sarvam chat streaming, embedding generation) can take 10-30s. Gunicorn's default worker timeout kills workers that don't respond within `timeout` seconds with SIGABRT (signal 6). With UvicornWorker, each worker handles many async requests concurrently, but if one slow request occupies a sync code path or there's a blocking init, the whole worker can appear stalled.

**Observed symptom:** Multiple SIGABRT entries per day in Cloud Run logs — `Worker (pid:XXX) was sent SIGABRT!` followed by `Uncaught signal: 6`. These cause brief 503s for in-flight requests served by the killed worker.

**How to apply:** In `apps/backend/gunicorn_conf.py`:
```python
timeout = 120       # was 30; AI requests can take 10-30s
graceful_timeout = 30  # keep short for clean shutdown
```
`graceful_timeout` controls how long gunicorn waits for workers to finish during SIGTERM (shutdown), not request timeout — keep it at 30.
