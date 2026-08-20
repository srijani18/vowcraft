#!/bin/sh
# Restore a dump produced by scripts/db-backup.sh.
#
#   sh scripts/db-restore.sh backups/vowcraft-20260819T173500Z.sql
#
# A full dump is applied to an empty database; an accounts-only dump is applied on top
# of an existing one. Either way this writes to the live database, so it asks first.

set -e

# NOTE: `docker compose exec -T` still forwards stdin, so every exec that does not
# genuinely read from it is redirected from /dev/null. Without that, an exec placed before
# a `read` prompt swallows the answer and the script aborts as though the user declined.
FILE="$1"

if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "Usage: sh scripts/db-restore.sh <dump.sql>"
  echo
  echo "Available:"
  ls -1 backups/*.sql 2>/dev/null | sed 's/^/  /' || echo "  (none — run scripts/db-backup.sh first)"
  exit 1
fi

if ! docker compose ps --status running --services 2>/dev/null | grep -q '^db$'; then
  echo "The db service is not running. Start it with: docker compose up -d"
  exit 1
fi

echo "About to apply $FILE to the live database."
printf 'Type yes to continue: '
read -r CONFIRM
[ "$CONFIRM" = "yes" ] || { echo "Aborted; nothing was changed."; exit 1; }

case "$FILE" in
  *-accounts-*)
    # The seed recreates `demo@vowcraft.test` on every boot, with a *new* id. An
    # accounts dump also contains that account, so ON CONFLICT skips the dumped User
    # row while its TeamMember and UserSettings rows still reference the dumped id —
    # which then fails a foreign key and aborts the restore.
    #
    # Removing the freshly seeded copy first lets the dump's version land intact,
    # children and all. It is the only account the seed owns, so this is the only row
    # that can collide this way.
    docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
      "DELETE FROM \"User\" WHERE email = 'demo@vowcraft.test';" > /dev/null 2>&1
    ;;
esac

# ON_ERROR_STOP so a partial restore fails loudly rather than leaving a half-populated
# database that looks fine. Accounts dumps use ON CONFLICT DO NOTHING, so rows that
# genuinely already exist are skipped rather than treated as errors.
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U vowcraft -d vowcraft < "$FILE" > /dev/null

case "$FILE" in
  *-accounts-*)
    # Deleting the seeded account above cascaded its transcripts and action items away,
    # and an accounts dump does not carry them (they are reproducible; an account is
    # not). Re-seed so the demo fixtures are back where they were.
    echo "→ re-seeding the demo fixtures"
    docker compose exec -T web node prisma/seed.mjs < /dev/null > /dev/null 2>&1  < /dev/null|| \
      echo "  (could not re-seed — run: sh scripts/reset-demo.sh)"
    ;;
esac

echo "→ restored. Accounts now present:"
docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
  'SELECT email FROM "User" ORDER BY "createdAt";' | sed 's/^/   /'
