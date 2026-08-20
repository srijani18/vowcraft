#!/bin/sh
# Destroy and rebuild the stack from scratch — backing up accounts first.
#
#   sh scripts/dev-reset.sh        (or: npm run dev:reset)
#
# Use this INSTEAD OF `docker compose down -v`. That command deletes the Postgres volume
# and every account in it, with no warning and no way back.
#
# Most of the time you do not want this at all: `sh scripts/reset-demo.sh` re-seeds the
# demo fixtures without touching accounts, which is all a clean test run needs.
#
# NOTE on `< /dev/null`: `docker compose exec -T` still forwards stdin, so an exec placed
# before a `read` prompt swallows the answer and the script aborts as though the user had
# declined. Every exec here that does not genuinely read stdin is redirected.

set -e

running() {
  docker compose ps --status running --services 2>/dev/null | grep -q "^$1\$"
}

ACCOUNTS=0
if running db; then
  ACCOUNTS=$(
    docker compose exec -T db psql -U voice2brd -d voice2brd -tAc \
      "SELECT count(*) FROM \"User\" WHERE email <> 'demo@voice2brd.test';" \
      < /dev/null 2>/dev/null | tr -d ' \r'
  )
fi

if [ "${ACCOUNTS:-0}" -gt 0 ]; then
  echo "→ ${ACCOUNTS} account(s) will be destroyed. Backing them up first."
  sh scripts/db-backup.sh accounts < /dev/null
  echo
  printf 'Type yes to destroy the volume anyway: '
  read -r CONFIRM
  if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted; nothing was changed."
    exit 1
  fi
fi

echo "→ destroying the volume"
docker compose down -v

echo "→ starting fresh"
docker compose up -d

printf '→ waiting for the app'
until curl -fsS http://localhost:3000/api/health 2>/dev/null | grep -q '"database":"up"'; do
  printf '.'
  sleep 2
done
echo ' ready'

LATEST=$(ls -1t backups/voice2brd-accounts-*.sql 2>/dev/null | head -1)
if [ -n "$LATEST" ] && [ "${ACCOUNTS:-0}" -gt 0 ]; then
  echo
  echo "Accounts were backed up. To bring them back:"
  echo "  sh scripts/db-restore.sh $LATEST"
fi
