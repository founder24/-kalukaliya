# NOTE: tried pinning to `python:3.12-slim-bookworm` (Debian 12) here on the
# theory that a more mature release would have fewer unpatched CVEs than the
# `slim` tag's current `trixie` (Debian 13) base — measured result was worse
# (added 5 critical-severity findings trixie didn't have, plus more highs).
# Both releases carry OS-package CVEs Debian's security team has not (and in
# some cases will never) issue a patch for; that's addressed in the scan
# workflow via `ignore-unfixed`, not by picking a different base image.
FROM python:3.12-slim as builder

WORKDIR /app

# `apt-get upgrade` pulls in the latest Debian security-team backports for
# every package already in the base image, not just the ones we install here.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip setuptools \
    && sed 's/ *\\$//' requirements.txt | grep -v '^\s*--hash' > requirements-nohash.txt \
    && pip install --no-cache-dir --user -r requirements-nohash.txt \
    && pip install --no-cache-dir --user "email-validator>=2.0"

FROM python:3.12-slim

WORKDIR /app

# The final stage starts from a fresh base image and never inherits the
# builder stage's system pip (only /root/.local is copied across), so the
# base image's bundled pip must be upgraded here too, not just in the
# builder — otherwise the shipped image still carries the base image's
# stale, vulnerable pip regardless of what the builder stage did.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip setuptools

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

USER appuser

COPY --from=builder /root/.local /home/appuser/.local

COPY app/ ./app/
COPY gunicorn_conf.py ./gunicorn_conf.py

ENV PATH=/home/appuser/.local/bin:$PATH

EXPOSE 8000

# Cloud Run injects PORT env var matching the --port flag (default 8080, or 8000 when --port=8000 is set).
# Using shell form so ${PORT:-8000} is expanded at runtime.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')"

CMD ["sh", "-c", "exec gunicorn app.main:app --bind 0.0.0.0:${PORT:-8000} -k uvicorn.workers.UvicornWorker -c gunicorn_conf.py"]
