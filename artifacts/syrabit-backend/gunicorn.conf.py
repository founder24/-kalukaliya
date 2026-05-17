import os
import multiprocessing

bind            = "0.0.0.0:" + os.environ.get("BACKEND_PORT", os.environ.get("PORT", "7766"))

# Dynamic worker count based on CPU cores for optimal throughput
# Formula: (2 × CPU cores) + 1 provides good balance for async workloads
workers         = int(os.environ.get("GUNICORN_WORKERS", str(multiprocessing.cpu_count() * 2 + 1)))
worker_class    = "uvicorn.workers.UvicornWorker"
# Reduce threads per worker, increase worker count for better async handling
threads         = int(os.environ.get("GUNICORN_THREADS", "2"))

# Optimized timeouts for faster failure detection and recovery
timeout         = 120  # Reduced from 300s for faster failure detection
graceful_timeout = 30  # Faster restarts during deploys
keepalive       = 5    # Reduced connection overhead

accesslog       = "-"
errorlog        = "-"
loglevel        = os.environ.get("LOG_LEVEL", "warning")

preload_app     = True

# Increased request limits for better stability under load
max_requests         = 10000  # Increased from 5000
max_requests_jitter  = 1000   # Prevents thundering herd on restart

worker_tmp_dir  = "/dev/shm" if os.path.isdir("/dev/shm") else None

# Connection limits for high-concurrency async workloads
worker_connections = 1000
