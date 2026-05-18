import multiprocessing

bind = "0.0.0.0:8000"
workers = multiprocessing.cpu_count() * 2 + 1
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
