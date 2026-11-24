# ---------- build stage ----------
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
# LiteLLM ships its proxy's admin web UI; this app never serves it, so drop it.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && rm -rf /install/lib/python3.12/site-packages/litellm/proxy/_experimental/out

# ---------- runtime stage ----------
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app ./app

# Non-root runtime user. /data is created so the named volume inherits its
# ownership when Docker initializes it from the image.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app

USER appuser

EXPOSE 8000

# $PORT lets PaaS platforms (Render/Railway/Fly/HF) override the port; Docker
# Compose uses the default 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,urllib.request; port=os.getenv('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status==200 else 1)"
