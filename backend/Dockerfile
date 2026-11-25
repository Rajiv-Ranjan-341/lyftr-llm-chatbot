# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- builder --
# Compiles wheels here so the runtime image needs no toolchain.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---------------------------------------------------------------- runtime --
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    DATABASE_URL=sqlite:////data/app.db \
    CACHE_DIR=/data/llm_cache

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

COPY app ./app

# Non-root runtime user. /data is created and owned here so an empty named
# volume mounted over it inherits the same ownership.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /data/llm_cache \
 && chown -R appuser:appuser /data /app
USER appuser

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=4).status==200 else 1)"

# $PORT is honoured so the same image runs on Render/Railway/Fly unchanged.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
