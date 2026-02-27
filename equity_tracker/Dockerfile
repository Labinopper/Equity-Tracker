# syntax=docker/dockerfile:1
# Equity Tracker — production Docker image
#
# Build:
#   docker build -t equity-tracker .
#
# Run (with persistent volume for the database):
#   docker run -d \
#     --name equity-tracker \
#     -p 8000:8000 \
#     -v equity-data:/data \
#     --env-file .env \
#     equity-tracker
#
# The database file lives at /data/portfolio.db (set EQUITY_DB_PATH=/data/portfolio.db).

# ---------------------------------------------------------------------------
# Stage 1: Builder — install Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml ./
COPY src/ ./src/

RUN python -m venv /build/venv \
    && /build/venv/bin/pip install --upgrade pip --quiet \
    && /build/venv/bin/pip install --no-cache-dir -e . \
    && /build/venv/bin/pip install --no-cache-dir sqlcipher3-binary

# ---------------------------------------------------------------------------
# Stage 2: Runtime — minimal image, non-root user
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# libsqlcipher0 is a belt-and-suspenders fallback; sqlcipher3-binary is self-contained
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlcipher0 \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user
RUN groupadd --system appuser && useradd --system --gid appuser appuser

WORKDIR /app

# Copy virtualenv from builder stage
COPY --from=builder /build/venv /app/venv

# Copy application source
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY run_api.py ./

# Persistent data directory — mount a Docker volume here
RUN mkdir -p /data && chown appuser:appuser /data

USER appuser

ENV PATH="/app/venv/bin:$PATH"
ENV PYTHONPATH="/app"

EXPOSE 8000

# Health check uses the unauthenticated /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

CMD ["python", "run_api.py"]
