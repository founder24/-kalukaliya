# Pinned to the `bookworm` (Debian 12) variant rather than the floating
# `python:3.12-slim` tag. `slim` now resolves to `trixie` (Debian 13), which
# is too new for Debian's security team to have backported fixes for most
# published CVEs yet — Trivy flags dozens of "no fix available" findings on
# trixie that bookworm (still receiving active security backports) resolves.
FROM python:3.12-slim-bookworm as builder

WORKDIR /app

# `apt-get upgrade` pulls in the latest Debian security-team backports for
# every package already in the base image, not just the ones we install here.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && sed 's/ *\\$//' requirements.txt | grep -v '^\s*--hash' > requirements-nohash.txt \
    && pip install --no-cache-dir --user -r requirements-nohash.txt \
    && pip install --no-cache-dir --user "email-validator>=2.0"

FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

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
