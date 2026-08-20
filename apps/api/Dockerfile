# Vowcraft API — FastAPI backend.
#
# Deployed on Render as its own service, separate from the Postgres instance and the
# Next.js frontend on Vercel. Local development runs the same image via docker-compose,
# so "works in dev" and "works in production" are the same claim.

FROM python:3.12-slim AS base

# Never buffer stdout: the structured JSON logger writes one line per event, and buffering
# would delay them arriving at whatever collects container logs, which defeats a log's
# purpose of being timely.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq for asyncpg's C extension; gcc for building it. Both stay in the final image
# because asyncpg needs libpq at runtime too, not only at build time.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    # The video-upload path demuxes with ffmpeg before anything reaches a transcription
    # provider (SPEC-010 §4.1) — without this, every video upload fails at the first
    # ffmpeg_available() check rather than transcoding.
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# packages/db first, and separately from requirements.txt: it changes far less often than
# application code, so this layer stays cached across most rebuilds.
COPY packages/db /app/packages/db
RUN pip install -e /app/packages/db

COPY apps/api/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

COPY apps/api /app
COPY docker/api-entrypoint.sh /app/entrypoint.sh

# Runs as a non-root user. The API terminates TLS nowhere — Render's edge does that — so
# there is no certificate material here that root access would matter for, but running
# unprivileged costs nothing and is the default worth keeping.
RUN useradd --create-home --uid 1000 api && chown -R api:api /app && chmod +x /app/entrypoint.sh
USER api

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
