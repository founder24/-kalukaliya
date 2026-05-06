# services/backend/Dockerfile
#
# Production image for the Python FastAPI backend on Azure Container Apps
# (Task #331). Mirrors the existing Railway Dockerfile at
# `artifacts/syrabit-backend/Dockerfile` with two DO-specific tweaks:
#
#   1. Health endpoint hits `/api/health` on $PORT (DO injects PORT=8080
#      via the spec; the FastAPI app reads PORT from env).
#   2. We bind to 0.0.0.0:$PORT explicitly because App Platform's
#      health check probes the platform-injected port and a default
#      127.0.0.1 bind would silently fail health checks.

FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libpq5 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY . .

RUN adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app
USER appuser

ENV PORT=8080 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
    CMD curl -fsS "http://localhost:${PORT}/api/health" || exit 1

CMD ["sh", "-c", "gunicorn server:app -c gunicorn.conf.py --bind 0.0.0.0:${PORT}"]
