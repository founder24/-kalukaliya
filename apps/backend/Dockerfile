FROM python:3.11-slim as builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Fix: pytest-asyncio 0.26 requires pytest<9, but requirements.txt pins pytest==9.0.3.
# Strip the conflicting pytest-asyncio block, install everything else, then add a compatible version.
RUN sed -i '/^pytest-asyncio==0\.26\.0/,/^[^ #]/{ /^pytest-asyncio/d; /^    --hash/d; /^    #/d; }' requirements.txt \
    && pip install --no-cache-dir --user -r requirements.txt \
    && pip install --no-cache-dir --user "pytest-asyncio>=1.0,<2" \
    && pip install --no-cache-dir --user "email-validator>=2.0"

FROM python:3.11-slim

WORKDIR /app

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

USER appuser

COPY --from=builder /root/.local /home/appuser/.local
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

COPY app/ ./app/
COPY gunicorn_conf.py ./gunicorn_conf.py

ENV PATH=/home/appuser/.local/bin:$PATH

EXPOSE 8000

CMD ["gunicorn", "app.main:app", "--bind", "0.0.0.0:8000", "-k", "uvicorn.workers.UvicornWorker", "-c", "gunicorn_conf.py"]
