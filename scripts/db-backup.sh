#!/bin/sh
# Dump the database to ./backups/, so an accidental volume wipe is recoverable.
#
#   sh scripts/db-backup.sh            → backups/voice2brd-<timestamp>.sql
#   sh scripts/db-backup.sh accounts   → only the tables an account lives in
#
# The volume survives `docker compose down`, restarts and rebuilds — it is destroyed only
# by `down -v` or `docker volume rm`. This exists because "only" is doing a lot of work in
# that sentence when the flag is one character.
#
# Dumps land on the host, so they outlive the volume.

set -e
MODE="${1:-full}"
OUT_DIR="backups"
STAMP=$(date -u '+%Y%m%dT%H%M%SZ')
mkdir -p "$OUT_DIR"

if ! docker compose ps --status running --services 2>/dev/null | grep -q '^db$'; then
  echo "The db service is not running. Start it with: docker compose up -d"
  exit 1
fi

if [ "$MODE" = "accounts" ]; then
  FILE="$OUT_DIR/voice2brd-accounts-$STAMP.sql"
  # Identity and settings only — the tables you would actually miss. Transcripts and
  # action items are reproducible by re-uploading; an account is not.
  #
  # `--inserts --on-conflict-do-nothing` rather than the default COPY: an accounts dump is
  # applied *on top of* a live database, where the seed has already recreated
  # `demo@voice2brd.test`. A COPY stream aborts on the first unique-key collision and
  # restores nothing; per-row inserts that skip conflicts restore everything else.
  docker compose exec -T db pg_dump -U voice2brd -d voice2brd --data-only \
    --inserts --on-conflict-do-nothing \
    -t '"User"' -t '"UserSettings"' -t '"TeamMember"' -t '"AuthIdentity"' -t '"Credential"' \
    < /dev/null > "$FILE"
else
  FILE="$OUT_DIR/voice2brd-$STAMP.sql"
  docker compose exec -T db pg_dump -U voice2brd -d voice2brd < /dev/null > "$FILE"
fi

echo "→ wrote $FILE ($(wc -c < "$FILE" | tr -d ' ') bytes)"

# Keep the 5 most recent of this kind. Every dump before the most recent is a strict
# subset of an account's history for recovery purposes — nothing is lost by pruning them,
# and 34 of these once accumulated silently before anyone noticed (they are gitignored,
# so nothing ever surfaced the pileup).
PRUNE_GLOB="$OUT_DIR/voice2brd-$([ "$MODE" = "accounts" ] && echo "accounts-")*.sql"
# shellcheck disable=SC2086
ls -1t $PRUNE_GLOB 2>/dev/null | tail -n +6 | xargs -r rm -f
echo "  restore with: sh scripts/db-restore.sh $FILE"
