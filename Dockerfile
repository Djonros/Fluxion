# ── deps ─────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLUXION_SERVER_MODE=saas

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# ── app code (runtime packages only) ─────────────────────────────────────────
COPY server/ server/
COPY core/ core/
COPY rag/ rag/
COPY orchestrator/ orchestrator/
COPY web/ web/
COPY config/ config/

RUN useradd --create-home fluxion && mkdir -p /app/data && chown -R fluxion:fluxion /app
USER fluxion

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "server.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
