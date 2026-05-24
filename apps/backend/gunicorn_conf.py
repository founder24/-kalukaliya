import os

bind = "0.0.0.0:8000"
# Default 2 workers suitable for 1Gi memory container (0.5 vCPU).
# Override via WEB_CONCURRENCY env var for larger containers.
# Formula: For 1Gi containers, use 2. For 2Gi containers, use 4.
workers = int(os.environ.get("WEB_CONCURRENCY", 2))
worker_class = "uvicorn.workers.UvicornWorker"
threads = 2
timeout = 30
keepalive = 5
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 50

accesslog = "-"
errorlog = "-"
loglevel = "info"
