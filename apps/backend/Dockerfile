FROM python:3.12-slim as builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN sed 's/ *\\$//' requirements.txt | grep -v '^\s*--hash' > requirements-nohash.txt \
    && pip install --no-cache-dir --user -r requirements-nohash.txt \
    && pip install --no-cache-dir --user "email-validator>=2.0"

FROM python:3.12-slim

WORKDIR /app

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
