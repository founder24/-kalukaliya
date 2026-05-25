FROM python:3.11-slim as builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Strip hashes from requirements.txt (consistent with CI approach) and remove
# test/dev dependencies not needed in production (pytest, coverage, etc.).
#
# MAINTENANCE NOTE: The grep -v -E pattern below excludes test-only packages
# from the production image. If you add a new test dependency to
# requirements.txt, you MUST also add it to the exclusion pattern here.
# Current exclusions: pytest, pytest-asyncio, pytest-cov, coverage, iniconfig, pluggy
# Example: adding pytest-xdist would require changing the pattern to:
#   '^(pytest|pytest-asyncio|pytest-cov|pytest-xdist|coverage|iniconfig|pluggy)=='
# A separate requirements-prod.txt would be a cleaner long-term solution.
RUN sed 's/ *\\$//' requirements.txt | grep -v '^\s*--hash' > requirements-nohash.txt \
    && grep -v -E '^(pytest|pytest-asyncio|pytest-cov|coverage|iniconfig|pluggy)==' requirements-nohash.txt > requirements-prod.txt \
    && pip install --no-cache-dir --user -r requirements-prod.txt \
    && pip install --no-cache-dir --user "email-validator>=2.0"

FROM python:3.11-slim

WORKDIR /app

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

USER appuser

COPY --from=builder /root/.local /home/appuser/.local

COPY app/ ./app/
COPY gunicorn_conf.py ./gunicorn_conf.py

ENV PATH=/home/appuser/.local/bin:$PATH

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

CMD ["gunicorn", "app.main:app", "--bind", "0.0.0.0:8000", "-k", "uvicorn.workers.UvicornWorker", "-c", "gunicorn_conf.py"]
