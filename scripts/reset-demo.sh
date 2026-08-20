#!/bin/sh
# Reset the seeded demo data WITHOUT destroying user accounts.
#
#   sh scripts/reset-demo.sh      (or: npm run demo:reset)
#
# Use this instead of `docker compose down -v` when you have accounts you care about.
# `down -v` deletes the Postgres volume, which takes every account with it — and the login
# screen then correctly reports "not recognised", which looks exactly like a bug and is
# not one.
#
# The seed only touches the demo account: it removes that account's transcripts, busy
# blocks and roster, then re-creates the fixture meetings. Accounts created by signing up
# are never referenced.

set -e

if ! docker compose ps --status running --services 2>/dev/null | grep -q '^web$'; then
  echo "The web service is not running. Start it with: docker compose up -d"
  exit 1
fi

echo "→ re-seeding the demo fixtures (user accounts are untouched)"
docker compose exec -T web node prisma/seed.mjs < /dev/null

echo "→ accounts still present:"
docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
  'SELECT email FROM "User" ORDER BY "createdAt";' \
  < /dev/null | sed 's/^/   /'
