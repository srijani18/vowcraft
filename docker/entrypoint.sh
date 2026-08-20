#!/bin/sh
set -e

# The standalone runner has no node_modules/.bin, so the Prisma CLI is invoked
# through its package entry point directly rather than via npx — npx would either
# fail to resolve it or try to download a second copy at boot.
PRISMA="node node_modules/prisma/build/index.js"

# Bring the schema up to date before serving. `db push` rather than
# `migrate deploy` because this repository ships the schema, not a migration
# history — the local Docker target is a working demo, and one command should
# produce it. A deployment with real data should switch this to `migrate deploy`.
echo "→ applying database schema"
$PRISMA db push --skip-generate --accept-data-loss

if [ "${SEED_ON_BOOT:-true}" = "true" ]; then
  echo "→ seeding demo data"
  node prisma/seed.mjs || echo "  (seed skipped — see the error above)"
fi

echo "→ starting web on :${PORT:-3000}"
exec "$@"
