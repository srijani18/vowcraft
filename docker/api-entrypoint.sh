#!/bin/sh
set -e

# `alembic upgrade head` before serving. Idempotent — running it against an already
# up-to-date database is a no-op — so it is safe to run on every container start rather
# than as a separate release step, for the same reason the Next.js entrypoint does the
# same with `prisma db push`: one command should produce a working local stack.
#
# A production deployment with more than one API instance should move this to a Render
# release-phase job instead, so N instances restarting together cannot race the same
# migration.
echo "→ applying database migrations"
cd /app/packages/db
# `web`'s own entrypoint runs `prisma db push --accept-data-loss` on every boot, which
# resyncs the database to schema.prisma — a schema that has never heard of
# `alembic_version`. Whichever service restarts second finds the tracking table gone and
# the real tables already there; `alembic upgrade head` alone cannot tell that apart from
# a genuinely fresh database and tries to recreate everything, "already exists" and all.
# See ensure_migration_state.py's docstring for the full account.
python3 ensure_migration_state.py
alembic upgrade head
cd /app

# `$PORT` because that is the contract every managed host uses: Render (and Fly, and
# Cloud Run) assign a port and expect the process to bind it, and a service listening
# somewhere else fails its health check while looking perfectly healthy in the logs.
# Defaults to 8000 so docker-compose.yml's fixed mapping is unchanged.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
