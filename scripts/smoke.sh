#!/bin/sh
# End-to-end verification of the acceptance criteria in SPEC-001 §12,
# SPEC-002 §9, and SPEC-004 §10, against a running stack.
#
#   docker compose up -d --build && sh scripts/smoke.sh
#
# Responses are always written to files and parsed from there, never passed
# through a shell variable: `echo` in POSIX sh interprets backslash escapes, and
# a JSON body containing "\n" would be corrupted into invalid JSON on the way.

set -u
BASE="${BASE:-http://localhost:3000}"
# The FastAPI backend, on its own origin. Speech moved there entirely (SPEC-014 §3) —
# there is no longer a Next.js route to check it through.
API_BASE="${API_BASE:-http://localhost:8000}"
TMP="${TMPDIR:-/tmp}/v2b-smoke"
mkdir -p "$TMP"
PASS=0
FAIL=0

green() { printf '\033[32m%s\033[0m\n' "$1"; }
red()   { printf '\033[31m%s\033[0m\n' "$1"; }
dim()   { printf '\033[2m%s\033[0m\n' "$1"; }
head_() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

# check <label> <result> [detail]
#
# NOTE: never write `check "... $(something)" $?` — a command substitution in the
# argument list runs *before* check is called and resets `$?` to its own exit status, so
# the assertion silently passes whatever happened. Nine checks were vacuous this way.
# Capture the status first:  STATUS=$?  then  check "…" "$STATUS".
check() {
  if [ "$2" = "0" ]; then
    green "  PASS  $1"; PASS=$((PASS + 1))
  else
    red   "  FAIL  $1"; [ $# -ge 3 ] && [ -n "$3" ] && dim "        $3"; FAIL=$((FAIL + 1))
  fi
}

# py <file> <expression> — evaluate a Python expression against parsed JSON.
# `d` is the document. Prints nothing and returns 1 on any error.
py() {
  python3 -c '
import sys, json
try:
    d = json.load(open(sys.argv[1]))
    out = eval(sys.argv[2])
    print("" if out is None else out)
except Exception:
    sys.exit(1)
' "$1" "$2" 2>/dev/null
}

# grep a response file for a literal string
has() { grep -q -- "$2" "$1" 2>/dev/null; }

# The action-items surface is served entirely by FastAPI (SPEC-015 §7) — a different
# origin, authenticated with a bearer token rather than the session cookie `$BASE` calls
# use. FastAPI has no dev-identity bypass (a deliberate choice: see the comment on
# `optional_user` in apps/api/app/api/dependencies.py), so reaching the seeded demo
# account's fixture data through it means a real login, same as any other account.
DEV_PASSWORD="${DEV_USER_PASSWORD:-vowcraft-dev-account-password}"
curl -fsS -X POST "$API_BASE/api/auth/login" \
  -H 'content-type: application/json' \
  -d "{\"email\":\"${DEV_USER_EMAIL:-demo@vowcraft.test}\",\"password\":\"$DEV_PASSWORD\"}" \
  -o "$TMP/ai-login.json" 2>/dev/null
AI_TOKEN=$(py "$TMP/ai-login.json" "d['tokens']['accessToken']")
AUTH_HEADER="authorization: Bearer $AI_TOKEN"

# The seed's execution fixtures are consumed by this suite (it really does approve
# and execute one item), so a second run against the same database would emit
# confusing per-test failures. Detect that up front and say what to do about it,
# rather than reporting eight failures that all mean "already run".
preflight() {
  curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items?limit=200" -o "$TMP/pre.json" 2>/dev/null || return 0
  LEFT=$(python3 -c "
import sys, json
d = json.load(open(sys.argv[1]))
print(sum(1 for i in d['items']
          if i['readiness'] == 'READY' and i['status'] == 'PROPOSED' and i['actionType'] == 'CALENDAR'))
" "$TMP/pre.json" 2>/dev/null || echo 1)
  if [ "${LEFT:-1}" = "0" ]; then
    printf '\n\033[33m  This database has already been through the suite — its execution fixtures are used up.\033[0m\n'
    printf '\033[2m  Reset and re-run:  npm run demo:reset && sh scripts/smoke.sh\033[0m\n'
  fi
}
preflight

# ── Snapshot accounts before doing anything.
#
# Every run of this suite has historically been followed by a `docker compose down -v`
# to get a clean fixture state — which destroys the volume and every account in it. A
# user lost their account three times that way, and each time the login screen correctly
# reported "not recognised" for an account that no longer existed.
#
# The dump lands on the *host*, so it survives a volume wipe. `reset-demo.sh` makes the
# wipe unnecessary in the first place; this is the net for when someone reaches for the
# flag anyway.
if command -v docker > /dev/null 2>&1; then
  REAL_ACCOUNTS=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT count(*) FROM \"User\" WHERE email NOT LIKE '%@acme.test' AND email <> 'demo@vowcraft.test';" \
    2>/dev/null | tr -d ' \r')
  if [ "${REAL_ACCOUNTS:-0}" -gt 0 ]; then
    sh scripts/db-backup.sh accounts > /dev/null 2>&1 &&
      dim "        backed up ${REAL_ACCOUNTS} real account(s) to backups/ before starting"
  fi
fi

head_ "health"
[ -n "$AI_TOKEN" ]
check "the demo account logs in against the FastAPI backend" $?
curl -fsS "$BASE/api/health" -o "$TMP/health.json" 2>/dev/null
check "health endpoint responds" $?
[ "$(py "$TMP/health.json" "d['database']")" = "up" ]
check "database reachable" $?
MODE=$(py "$TMP/health.json" "d['integrationsMode']")
dim "        integrations mode: ${MODE:-unknown}"
[ "$(py "$TMP/health.json" "d['encryptionConfigured']")" = "True" ]
check "APP_ENCRYPTION_KEY configured (SPEC-004 §10.1)" $?
[ "$(py "$TMP/health.json" "len(d['providers'])")" = "5" ]
check "five providers registered (Gmail and SendGrid both serve EMAIL)" $?

head_ "listing and grouping (SPEC-001 §12.1)"
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items?limit=200" -o "$TMP/list.json"
TOTAL=$(py "$TMP/list.json" "d['counts']['total']")
[ "${TOTAL:-0}" -gt 0 ]
check "action items returned (total=${TOTAL:-0})" $?

TID=$(py "$TMP/list.json" "next((t['id'] for t in d['facets']['transcripts'] if t['title']=='Q3 budget sync'), '')")
if [ -n "$TID" ]; then
  curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items?transcriptId=$TID&limit=200" -o "$TMP/meeting.json"
  R=$(py "$TMP/meeting.json" "d['counts']['READY']")
  N=$(py "$TMP/meeting.json" "d['counts']['NEEDS_CLARIFICATION']")
  I=$(py "$TMP/meeting.json" "d['counts']['INFORMATIONAL']")
  [ "$R" = "4" ] && [ "$N" = "2" ] && [ "$I" = "3" ]
  check "Q3 budget sync groups 4/2/3 (got $R/$N/$I)" $?
else
  check "Q3 budget sync transcript present" 1 "seed may not have run"
fi

head_ "derived fields (SPEC-001 §10)"
for field in readiness riskTier violations missingFields approvalGate canExecute; do
  has "$TMP/list.json" "\"$field\""
  check "$field computed server-side" $?
done
has "$TMP/list.json" '"sourceTimestampLabel"'
check "source timestamp pre-formatted for display" $?

head_ "filters compose and are authoritative (SPEC-001 §12.5)"
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items?readiness=READY&priority=HIGH&limit=200" -o "$TMP/filtered.json"
[ "$(py "$TMP/filtered.json" "sum(1 for i in d['items'] if i['readiness']!='READY' or i['priority']!='HIGH')")" = "0" ]
check "readiness+priority filter returns only matching items" $?
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items?type=CALENDAR&limit=200" -o "$TMP/cal.json"
[ "$(py "$TMP/cal.json" "sum(1 for i in d['items'] if i['actionType']!='CALENDAR')")" = "0" ]
check "type filter returns only calendar actions" $?
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items?q=budget&limit=200" -o "$TMP/q.json"
[ "$(py "$TMP/q.json" "d['counts']['total']")" -gt 0 ]
check "text search matches descriptions and quotes" $?

head_ "illegal transition rejected (SPEC-001 §12.3)"
ANY_ID=$(py "$TMP/list.json" "d['items'][0]['id']")
CODE=$(curl -s -o "$TMP/patch.json" -w '%{http_code}' -X PATCH \
  -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"status":"EXECUTED"}' \
  "$API_BASE/api/action-items/$ANY_ID")
[ "$CODE" = "422" ]
check "PATCH status=EXECUTED returns 422 (got $CODE)" $?
has "$TMP/patch.json" 'illegal_transition'
check "error code is illegal_transition" $?

head_ "guardrails block execution (SPEC-003 §10.1)"
WEEKEND_ID=$(py "$TMP/list.json" "next((i['id'] for i in d['items'] if 'Saturday' in i['description']), '')")
if [ -n "$WEEKEND_ID" ]; then
  CODE=$(curl -s -o "$TMP/guard.json" -w '%{http_code}' -X POST \
    -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{}' \
    "$API_BASE/api/action-items/$WEEKEND_ID/execute")
  [ "$CODE" = "422" ]
  check "approved weekend + over-length meeting still refused (got $CODE)" $?
  has "$TMP/guard.json" 'guardrail_blocked'
  check "error code is guardrail_blocked" $?
  grep -qE 'SCHED_WEEKEND|SCHED_MAX_DURATION' "$TMP/guard.json"
  check "response names the rule that blocked it" $?
else
  check "weekend guardrail fixture present" 1
fi

head_ "external email escalates to HIGH risk (SPEC-003 §10.2)"
EXT_ID=$(py "$TMP/list.json" "next((i['id'] for i in d['items'] if 'vendor' in i['description'].lower() and i['actionType']=='EMAIL'), '')")
if [ -n "$EXT_ID" ]; then
  curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items/$EXT_ID" -o "$TMP/ext.json"
  [ "$(py "$TMP/ext.json" "d['riskTier']")" = "HIGH" ]
  check "external send classified HIGH risk" $?
  [ "$(py "$TMP/ext.json" "d['approvalGate']")" = "EXPLICIT_APPROVAL_WITH_CONFIRMATION" ]
  check "gate requires typed confirmation" $?
  [ "$(py "$TMP/ext.json" "d['readiness']")" = "NEEDS_CLARIFICATION" ]
  check "low confidence keeps it out of the ready lane" $?
else
  check "external email fixture present" 1
fi

head_ "dependency gate (SPEC-002 §8)"
DEP_ID=$(py "$TMP/list.json" "next((i['id'] for i in d['items'] if i.get('dependsOn') and i['status']=='APPROVED'), '')")
if [ -n "$DEP_ID" ]; then
  CODE=$(curl -s -o "$TMP/dep.json" -w '%{http_code}' -X POST \
    -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{}' "$API_BASE/api/action-items/$DEP_ID/execute")
  [ "$CODE" = "409" ]
  check "blocked-by-dependency refused (got $CODE)" $?
  has "$TMP/dep.json" 'blocked_by_dependency'
  check "error code is blocked_by_dependency" $?
else
  check "dependency fixture present" 1
fi

head_ "superseded action blocked (SPEC-003 §4 POL_SUPERSEDED)"
SUP_ID=$(py "$TMP/list.json" "next((i['id'] for i in d['items'] if i.get('supersededBy')), '')")
if [ -n "$SUP_ID" ]; then
  grep -q 'POL_SUPERSEDED' "$TMP/list.json"
  check "superseded item carries POL_SUPERSEDED" $?
else
  check "superseded fixture present" 1
fi

head_ "approval gate (SPEC-002 §5 step 7)"
READY_ID=$(py "$TMP/list.json" "next((i['id'] for i in d['items'] if i['readiness']=='READY' and i['status']=='PROPOSED' and i['actionType']=='CALENDAR'), '')")
if [ -n "$READY_ID" ]; then
  CODE=$(curl -s -o "$TMP/na.json" -w '%{http_code}' -X POST \
    -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{}' "$API_BASE/api/action-items/$READY_ID/execute")
  [ "$CODE" = "409" ]
  check "unapproved item refuses to execute (got $CODE)" $?
  has "$TMP/na.json" 'not_approved'
  check "error code is not_approved" $?

  head_ "dry run (SPEC-002 §9.4)"
  curl -fsS -X POST -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"dryRun":true}' \
    "$API_BASE/api/action-items/$READY_ID/execute" -o "$TMP/dry.json"
  [ "$(py "$TMP/dry.json" "d['dryRun']")" = "True" ]
  check "dry run returns a preview" $?
  [ -n "$(py "$TMP/dry.json" "d['preview']['consequence']")" ]
  check "preview states the consequence in words" $?
  [ "$(py "$TMP/dry.json" "len(d['preview']['fields'])")" -gt 2 ]
  check "preview lists the exact payload fields" $?
  curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items/$READY_ID" -o "$TMP/after-dry.json"
  AFTER=$(py "$TMP/after-dry.json" "d['status']")
  [ "$AFTER" = "PROPOSED" ]
  check "dry run left status unchanged (still $AFTER)" $?

  head_ "approve then execute (SPEC-002 §9.1)"
  curl -fsS -X PATCH -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"status":"APPROVED"}' \
    "$API_BASE/api/action-items/$READY_ID" -o "$TMP/approve.json"
  [ "$(py "$TMP/approve.json" "d['status']")" = "APPROVED" ]
  check "approve succeeds and is reflected in the DTO" $?

  curl -s -X POST -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"confirmed":true}' \
    "$API_BASE/api/action-items/$READY_ID/execute" -o "$TMP/exec.json"
  [ "$(py "$TMP/exec.json" "d['result']['outcome']")" = "SUCCESS" ]
  check "execution succeeded in mock mode" $?
  [ -n "$(py "$TMP/exec.json" "d['result']['externalId']")" ]
  check "executionResult carries an externalId" $?
  [ "$(py "$TMP/exec.json" "d['result']['simulated']")" = "True" ]
  check "result is labelled simulated in mock mode" $?
  [ "$(py "$TMP/exec.json" "len(d['result']['payloadUsed'])")" -gt 0 ]
  check "executionResult records payloadUsed for reproducibility" $?
  [ "$(py "$TMP/exec.json" "d['result']['version']")" = "1" ]
  check "executionResult is versioned (SPEC-002 §7)" $?
  [ "$(py "$TMP/exec.json" "d['status']")" = "EXECUTED" ]
  check "status advanced to EXECUTED" $?

  head_ "idempotency (SPEC-002 §9.2)"
  EXT_ID_1=$(py "$TMP/exec.json" "d['result']['externalId']")
  curl -s -X POST -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"confirmed":true}' \
    "$API_BASE/api/action-items/$READY_ID/execute" -o "$TMP/again.json"
  [ "$(py "$TMP/again.json" "d['replayed']")" = "True" ]
  check "identical repeat replays instead of duplicating" $?
  [ "$(py "$TMP/again.json" "d['result']['externalId']")" = "$EXT_ID_1" ]
  check "replay returns the original external id ($EXT_ID_1)" $?

  # A *different* payload on an executed item must not slip through as a replay.
  CODE=$(curl -s -o "$TMP/diff.json" -w '%{http_code}' -X POST \
    -H "$AUTH_HEADER" -H 'content-type: application/json' \
    -d '{"payloadOverride":{"title":"Something else entirely"},"confirmed":true}' \
    "$API_BASE/api/action-items/$READY_ID/execute")
  [ "$CODE" = "409" ] && has "$TMP/diff.json" 'already_executed'
  check "different payload on an executed item returns 409 (got $CODE)" $?

  head_ "audit trail (SPEC-003 §10.4)"
  ATTEMPTS=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT count(*) FROM \"ExecutionAttempt\" WHERE \"actionItemId\"='$READY_ID' AND outcome='SUCCESS';" 2>/dev/null | tr -d ' \r')
  [ "${ATTEMPTS:-0}" = "1" ]
  check "exactly one SUCCESS attempt recorded despite two requests (got ${ATTEMPTS:-?})" $?
  EXECUTED_LOGS=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT count(*) FROM \"AuditLog\" WHERE \"actionItemId\"='$READY_ID' AND event='action_item.executed';" 2>/dev/null | tr -d ' \r')
  [ "${EXECUTED_LOGS:-0}" = "1" ]
  check "exactly one action_item.executed audit row (got ${EXECUTED_LOGS:-?})" $?
  BLOCKED_LOGS=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT count(*) FROM \"AuditLog\" WHERE event='action_item.guardrail_blocked';" 2>/dev/null | tr -d ' \r')
  [ "${BLOCKED_LOGS:-0}" -ge 1 ]
  check "blocked attempt still wrote an audit row (SPEC-003 §10.3)" $?
  CORRECTIONS=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT count(*) FROM \"Correction\";" 2>/dev/null | tr -d ' \r')
  dim "        corrections captured so far: ${CORRECTIONS:-0}"
else
  check "executable calendar fixture present" 1
fi

head_ "bulk operations (SPEC-001 §9)"
BULK_IDS=$(py "$TMP/list.json" "','.join(i['id'] for i in d['items'] if i['status']=='PROPOSED' and i['readiness']=='READY')")
if [ -n "$BULK_IDS" ]; then
  python3 -c '
import sys, json
print(json.dumps({"ids": sys.argv[1].split(","), "op": "defer"}))' "$BULK_IDS" > "$TMP/bulk-body.json"
  curl -fsS -X POST -H "$AUTH_HEADER" -H 'content-type: application/json' --data-binary "@$TMP/bulk-body.json" \
    "$API_BASE/api/action-items/bulk" -o "$TMP/bulk.json"
  [ "$(py "$TMP/bulk.json" "d['okCount']")" -gt 0 ]
  STATUS=$?
  check "bulk defer applied to $(py "$TMP/bulk.json" "d['okCount']") item(s)" "$STATUS"
  [ -n "$(py "$TMP/bulk.json" "len(d['results'])")" ]
  check "per-item outcomes returned" $?
else
  dim "        (no bulk fixture left after earlier steps; skipped)"
fi

head_ "credential vault (SPEC-004 §10)"
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/credentials" -o "$TMP/creds.json"
check "credential catalogue served" $?
COUNT=$(py "$TMP/creds.json" "len(d['credentials'])")
[ "${COUNT:-0}" -ge 20 ]
check "catalogue lists ${COUNT:-0} providers" $?
FREE=$(py "$TMP/creds.json" "sum(1 for c in d['credentials'] if c['tier'] in ('free','local'))")
[ "${FREE:-0}" -ge 8 ]
check "${FREE:-0} free or local providers available" $?
[ "$(py "$TMP/creds.json" "len(d['modules'])")" = "5" ]
check "all five modules report availability" $?

SECRET="gsk_smoketest_abcdef0123456789"
curl -s -X PUT -H "$AUTH_HEADER" -H 'content-type: application/json' \
  -d "{\"secrets\":{\"apiKey\":\"$SECRET\"}}" \
  "$API_BASE/api/credentials/groq" -o "$TMP/saved.json"

# One account can cover several capabilities: a Groq key serves both transcription and
# extraction. Entering it once must satisfy both, or the pipeline reports "no extraction
# provider is configured" to someone who has plainly configured one.
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/credentials" -o "$TMP/shared.json"
[ "$(py "$TMP/shared.json" "next(c['source'] for c in d['credentials'] if c['service']=='groq_llm')")" = "USER" ]
check "one Groq key also satisfies the extraction entry" $?
[ -n "$(py "$TMP/shared.json" "next(c['sharedFrom'] for c in d['credentials'] if c['service']=='groq_llm') or ''")" ]
check "…and the UI is told which key covered it" $?
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/transcripts/pipeline-status" -o "$TMP/pipe2.json"
[ "$(py "$TMP/pipe2.json" "d['extract']['available']")" = "True" ]
check "…so the extraction stage reports itself available" $?
[ -n "$(py "$TMP/saved.json" "d['hints']['apiKey']")" ]
STATUS=$?
check "saving a key returns a masked hint ($(py "$TMP/saved.json" "d['hints']['apiKey']"))" "$STATUS"
grep -q -- "$SECRET" "$TMP/saved.json" && LEAKED=0 || LEAKED=1
[ "$LEAKED" = "1" ]
check "plaintext secret absent from the response (SPEC-004 §10.2)" $?
[ "$(py "$TMP/saved.json" "d['source']")" = "USER" ]
check "user key takes precedence over environment (SPEC-004 §10.4)" $?

head_ "ciphertext is bound to its owner (SPEC-004 §10.3)"
if command -v docker > /dev/null 2>&1; then
  psql() { docker compose exec -T db psql -U vowcraft -d vowcraft -tAc "$1" 2>/dev/null | tr -d ' \r'; }
  CT=$(psql 'SELECT "secretsEnc" FROM "Credential" WHERE service='"'"'groq'"'"';')
  case "$CT" in v1.*) BOUND=0 ;; *) BOUND=1 ;; esac
  [ "$BOUND" = "0" ]
  check "stored value is a v1 AES-256-GCM envelope, not plaintext" $?
  printf '%s' "$CT" | grep -q -- "$SECRET" && CTLEAK=0 || CTLEAK=1
  [ "$CTLEAK" = "1" ]
  check "plaintext absent from the ciphertext column" $?

  # Move the row to a different service: the AAD no longer matches, so it must
  # fail to decrypt rather than silently working under the wrong identity.
  psql 'UPDATE "Credential" SET service='"'"'mistral'"'"', module='"'"'EXTRACTION'"'"' WHERE service='"'"'groq'"'"';' > /dev/null
  curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/credentials" -o "$TMP/moved.json"
  [ "$(py "$TMP/moved.json" "next(c['source'] for c in d['credentials'] if c['service']=='mistral')")" = "NONE" ]
  check "row moved to another service reports source NONE" $?
  [ "$(py "$TMP/moved.json" "next(c['status'] for c in d['credentials'] if c['service']=='mistral')")" = "INVALID" ]
  check "undecryptable row reports INVALID, never 'configured'" $?
  psql 'DELETE FROM "Credential" WHERE service='"'"'mistral'"'"';' > /dev/null
else
  dim "        (docker unavailable; skipped)"
fi

head_ "deletion falls back down the resolution order"
curl -s -X PUT -H "$AUTH_HEADER" -H 'content-type: application/json' \
  -d "{\"secrets\":{\"apiKey\":\"$SECRET\"}}" "$API_BASE/api/credentials/groq" -o /dev/null
curl -s -X DELETE -H "$AUTH_HEADER" "$API_BASE/api/credentials/groq" -o "$TMP/deleted.json"
[ "$(py "$TMP/deleted.json" "d['source']")" = "NONE" ]
check "deleting the key falls back to NONE (no env key present)" $?

head_ "secrets never reach the logs (SPEC-004 §10.2)"
if command -v docker > /dev/null 2>&1; then
  # Credentials now live on FastAPI (SPEC-015 §7), so this is the api container's log.
  docker compose logs api 2>/dev/null | grep -q -- "$SECRET" && LOGLEAK=0 || LOGLEAK=1
  [ "$LOGLEAK" = "1" ]
  check "plaintext secret absent from container logs" $?
else
  dim "        (docker unavailable; skipped)"
fi

head_ "landing overview agrees with the board (SPEC-005 §9.8)"
curl -fsS "$BASE/api/overview" -o "$TMP/overview.json"
check "overview endpoint responds" $?
for group in READY NEEDS_CLARIFICATION INFORMATIONAL; do
  OV=$(py "$TMP/overview.json" "d['byReadiness']['$group']")
  BD=$(py "$TMP/list.json" "d['counts']['$group']")
  [ "$OV" = "$BD" ]
  check "$group matches on both surfaces (overview=$OV board=$BD)" $?
done
[ "$(py "$TMP/overview.json" "len(d['funnel'])")" = "4" ]
check "workflow funnel has all four stages" $?
[ -n "$(py "$TMP/overview.json" "d['rates']['executionSuccessRate']")" ]
check "execution success rate computed" $?
[ "$(py "$TMP/overview.json" "len(d['system']['providers'])")" = "5" ]
check "five providers registered (Gmail + SendGrid both serve EMAIL)" $?

head_ "profile: rename (SPEC-005 §9.4)"
# Against a throwaway with its own session, not the dev identity. Renaming the seeded
# account left it called "Smoke Reviewer", which then showed up as the profile name for
# anyone whose session had been dropped — confusing, and not the suite's business.
#
# Profile now lives on FastAPI (SPEC-015 §7), authenticated with a bearer token rather
# than a Next.js session cookie — created directly there, since nothing downstream needs
# the cookie.
RENAME_EMAIL="renametest-$$@acme.test"
curl -s -X POST "$API_BASE/api/auth/signup" -H 'content-type: application/json' \
  -d "{\"name\":\"Before Rename\",\"email\":\"$RENAME_EMAIL\",\"password\":\"rename throwaway phrase\"}" \
  -o "$TMP/rename-signup.json"
RENAME_AUTH="authorization: Bearer $(py "$TMP/rename-signup.json" "d['tokens']['accessToken']")"
curl -fsS -H "$RENAME_AUTH" -X PATCH -H 'content-type: application/json' -d '{"name":"After Rename"}' \
  "$API_BASE/api/profile" -o "$TMP/profile.json"
[ "$(py "$TMP/profile.json" "d['name']")" = "After Rename" ]
check "display name updated" $?
[ "$(py "$TMP/profile.json" "d['email']")" = "$RENAME_EMAIL" ]
check "…on the intended account, not the dev identity" $?
grep -q 'passwordHash' "$TMP/profile.json" && HASHLEAK=0 || HASHLEAK=1
[ "$HASHLEAK" = "1" ]
check "passwordHash absent from the response (SPEC-005 §9.3)" $?

head_ "profile: password policy (SPEC-005 §9.1, §9.2)"
#
# Runs against a throwaway account this block creates, with its own session — never
# against the development identity and never with an unscoped UPDATE.
#
# An earlier version did `UPDATE "User" SET "passwordHash" = NULL` with no WHERE
# clause, to get a known starting state. That wiped the password of *every* account in
# the database, including any a real person had signed up with: their login then
# correctly reported "not recognised" because the account genuinely had no password.
# A test script must not be able to do that to data it did not create.
#
PW_EMAIL="pwtest-$$@acme.test"
PW2_EMAIL="pwtest2-$$@acme.test"
PW_JAR="$TMP/pw-jar.txt"
PW2_JAR="$TMP/pw2-jar.txt"
rm -f "$PW_JAR" "$PW2_JAR"

# Both accounts are created up front, so clearing one gives a meaningful control: the
# scoped UPDATE must leave the sibling's hash intact. Asserting against "any other
# account" was wrong — at this point in the suite the only other account is the seeded
# one, which legitimately has no password.
curl -s -c "$PW_JAR" -o /dev/null -X POST -H 'content-type: application/json' \
  -d "{\"name\":\"Password Tester\",\"email\":\"$PW_EMAIL\",\"password\":\"initial throwaway phrase\"}" \
  "$BASE/api/auth/signup"
curl -s -c "$PW2_JAR" -o /dev/null -X POST -H 'content-type: application/json' \
  -d "{\"name\":\"Rotation Tester\",\"email\":\"$PW2_EMAIL\",\"password\":\"first throwaway phrase\"}" \
  "$BASE/api/auth/signup"
grep -q v2b_session "$PW_JAR" && grep -q v2b_session "$PW2_JAR"
check "two throwaway accounts were created for the password tests" $?

# Clear ONE account's password, scoped by email to the row this block created.
if command -v docker > /dev/null 2>&1; then
  docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "UPDATE \"User\" SET \"passwordHash\" = NULL, \"passwordUpdatedAt\" = NULL WHERE email = '$PW_EMAIL';" > /dev/null 2>&1

  CLEARED=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT COALESCE(\"passwordHash\", 'CLEARED') FROM \"User\" WHERE email = '$PW_EMAIL';" 2>/dev/null | tr -d ' \r')
  [ "$CLEARED" = "CLEARED" ]
  check "the named throwaway had its password cleared" $?

  # The control: the sibling created moments earlier must be untouched. This is the
  # regression guard for the unscoped UPDATE that wiped every account in the database.
  SIBLING=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT COALESCE(left(\"passwordHash\", 3), 'CLEARED') FROM \"User\" WHERE email = '$PW2_EMAIL';" 2>/dev/null | tr -d ' \r')
  [ "$SIBLING" = "s1." ]
  check "the sibling account kept its password (got '$SIBLING')" $?
fi

# Password change now lives on FastAPI's auth surface (SPEC-015 §7) — mint this
# account its own bearer token rather than reusing its Next.js session cookie.
curl -s -X POST "$API_BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$PW2_EMAIL\",\"password\":\"first throwaway phrase\"}" -o "$TMP/pw2-login.json"
PW2_AUTH="authorization: Bearer $(py "$TMP/pw2-login.json" "d['tokens']['accessToken']")"

pw() { # pw <json-body> ; sets CODE, writes $TMP/pw.json, uses the throwaway's bearer token
  CODE=$(curl -s -H "$PW2_AUTH" -o "$TMP/pw.json" -w '%{http_code}' -X POST \
    -H 'content-type: application/json' -d "$1" "$API_BASE/api/auth/change-password")
}

pw '{"currentPassword":"first throwaway phrase","newPassword":"short"}'
[ "$CODE" = "422" ] && has "$TMP/pw.json" 'password_too_short'
check "a password under 12 characters is refused (got $CODE)" $?

pw "{\"currentPassword\":\"first throwaway phrase\",\"newPassword\":\"pwtest2-$$-mypassword\"}"
[ "$CODE" = "422" ] && has "$TMP/pw.json" 'password_contains_email'
check "a password containing the email local part is refused" $?

pw '{"newPassword":"another perfectly fine phrase"}'
[ "$CODE" = "403" ] && has "$TMP/pw.json" 'current_password_required'
check "rotating without the current password returns 403" $?

pw '{"currentPassword":"definitely not it","newPassword":"another perfectly fine phrase"}'
[ "$CODE" = "403" ] && has "$TMP/pw.json" 'current_password_incorrect'
check "a wrong current password returns 403" $?

pw '{"currentPassword":"first throwaway phrase","newPassword":"first throwaway phrase"}'
[ "$CODE" = "422" ] && has "$TMP/pw.json" 'password_unchanged'
check "reusing the current password is refused" $?

pw '{"currentPassword":"first throwaway phrase","newPassword":"an entirely different phrase"}'
[ "$CODE" = "200" ]
check "a correct rotation succeeds (got $CODE)" $?

# The rotation is real: the new password signs in, the old one does not.
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' \
  -d "{\"email\":\"$PW2_EMAIL\",\"password\":\"an entirely different phrase\"}" "$BASE/api/auth/login")
[ "$CODE" = "200" ]
check "the rotated password signs in (got $CODE)" $?
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' \
  -d "{\"email\":\"$PW2_EMAIL\",\"password\":\"first throwaway phrase\"}" "$BASE/api/auth/login")
[ "$CODE" = "401" ]
check "the superseded password does not (got $CODE)" $?

if command -v docker > /dev/null 2>&1; then
  docker compose logs web 2>/dev/null | grep -q 'first throwaway phrase' && PWLEAK=0 || PWLEAK=1
  [ "$PWLEAK" = "1" ]
  check "no password value reaches the logs" $?
  STORED=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT \"passwordHash\" FROM \"User\" WHERE email = '$PW2_EMAIL';" 2>/dev/null | tr -d ' \r')
  case "$STORED" in s1.*) SCRYPT=0 ;; *) SCRYPT=1 ;; esac
  [ "$SCRYPT" = "0" ]
  check "stored value is a versioned scrypt hash, not plaintext" $?
fi

head_ "the suite leaves pre-existing accounts alone"
# The regression guard for the bug above: the seeded account must still be able to sign
# in with the password it was seeded with, after everything this suite has done.
if command -v docker > /dev/null 2>&1; then
  DEMO_HASH=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT COALESCE(left(\"passwordHash\", 3), 'NONE') FROM \"User\" WHERE email = '${DEV_EMAIL:-demo@vowcraft.test}';" 2>/dev/null | tr -d ' \r')
  # Either it never had one (seeded accounts do not) or it still has a valid hash —
  # what must NOT happen is a hash being destroyed by this script.
  case "$DEMO_HASH" in s1.|NONE) SAFE=0 ;; *) SAFE=1 ;; esac
  [ "$SAFE" = "0" ]
  check "the seeded account's password state was not corrupted (${DEMO_HASH})" $?
fi

head_ "onboarding persists (SPEC-005 §9.5)"
curl -fsS -X POST -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"action":"skip","reachedStep":1}' \
  "$API_BASE/api/profile/onboarding" -o "$TMP/tour.json"
[ "$(py "$TMP/tour.json" "d['onboardingSkipped']")" = "True" ]
check "skip is recorded" $?
[ "$(py "$TMP/tour.json" "d['onboardingCompletedAt'] is not None")" = "True" ]
check "skip is terminal — the tour will not reappear" $?
curl -fsS -X POST -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"action":"complete"}' \
  "$API_BASE/api/profile/onboarding" -o "$TMP/tour2.json"
[ "$(py "$TMP/tour2.json" "d['onboardingSkipped']")" = "False" ]
check "complete clears the skipped flag" $?

head_ "account deletion is two-step (SPEC-005 §9.7)"
CODE=$(curl -s -H "$RENAME_AUTH" -o "$TMP/del.json" -w '%{http_code}' -X DELETE \
  -H 'content-type: application/json' -d '{"confirm":"wrong@example.com"}' "$API_BASE/api/profile")
[ "$CODE" = "422" ] && has "$TMP/del.json" 'confirmation_mismatch'
check "a mismatched confirmation changes nothing (got $CODE)" $?
EMAIL=$(py "$TMP/profile.json" "d['email']")   # the rename throwaway
curl -fsS -H "$RENAME_AUTH" -X DELETE -H 'content-type: application/json' -d "{\"confirm\":\"$EMAIL\"}" \
  "$API_BASE/api/profile" -o "$TMP/del2.json"
[ -n "$(py "$TMP/del2.json" "d['deletionEffectiveAt']")" ]
check "correct confirmation schedules deletion with a grace period" $?
curl -fsS -H "$RENAME_AUTH" -X POST "$API_BASE/api/profile/restore" -o "$TMP/del3.json"
[ "$(py "$TMP/del3.json" "d['deletionRequestedAt'] is None")" = "True" ]
check "deletion can be cancelled inside the window" $?

head_ "preferences protect the rule engine (SPEC-005 §4.1)"
pref() {
  CODE=$(curl -s -o "$TMP/pref.json" -w '%{http_code}' -X PATCH \
    -H "$AUTH_HEADER" -H 'content-type: application/json' -d "$1" "$API_BASE/api/settings")
}
pref '{"workdayStart":"25:00"}'
[ "$CODE" = "422" ] && has "$TMP/pref.json" 'invalid_clock'
check "a malformed clock is refused rather than disabling SCHED_HOURS" $?
pref '{"timeZone":"Mars/Olympus"}'
[ "$CODE" = "422" ] && has "$TMP/pref.json" 'invalid_time_zone'
check "an unknown time zone is refused" $?
pref '{"workdayStart":"18:00","workdayEnd":"09:00"}'
[ "$CODE" = "422" ] && has "$TMP/pref.json" 'invalid_workday'
check "an inverted working day is refused" $?
pref '{"orgDomains":["not a domain"]}'
[ "$CODE" = "422" ] && has "$TMP/pref.json" 'invalid_domain'
check "a malformed org domain is refused" $?
pref '{"providerRouting":{"EMAIL":"notion"}}'
[ "$CODE" = "422" ] && has "$TMP/pref.json" 'invalid_routing'
check "routing EMAIL to a task provider is refused" $?

head_ "email provider routing: Gmail and SendGrid (SPEC-005 §9.6)"
curl -fsS -X PATCH -H "$AUTH_HEADER" -H 'content-type: application/json' \
  -d '{"providerRouting":{"EMAIL":"sendgrid"},"orgDomains":["acme.test"]}' \
  "$API_BASE/api/settings" -o "$TMP/route.json"
[ "$(py "$TMP/route.json" "d['providerRouting']['EMAIL']")" = "sendgrid" ]
check "EMAIL routed to SendGrid" $?
[ "$(py "$TMP/route.json" "len(d['routingOptions'])")" -ge 1 ]
check "the UI is offered a choice for EMAIL" $?

DRAFT_ID=$(py "$TMP/list.json" "next((i['id'] for i in d['items'] if i['actionType']=='EMAIL' and i['payload'].get('sendMode')=='draft'), '')")
if [ -n "$DRAFT_ID" ]; then
  CODE=$(curl -s -o "$TMP/sgdraft.json" -w '%{http_code}' -X POST \
    -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"dryRun":true}' \
    "$API_BASE/api/action-items/$DRAFT_ID/execute")
  [ "$CODE" = "422" ]
  check "SendGrid refuses a draft payload rather than delivering it (got $CODE)" $?
  grep -q 'cannot save drafts' "$TMP/sgdraft.json"
  check "the refusal states the reason and the remedy" $?

  curl -fsS -X PATCH -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"providerRouting":{"EMAIL":"gmail"}}' \
    "$API_BASE/api/settings" -o /dev/null
  curl -fsS -X POST -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"dryRun":true}' \
    "$API_BASE/api/action-items/$DRAFT_ID/execute" -o "$TMP/gmdraft.json"
  [ "$(py "$TMP/gmdraft.json" "d['riskTier']")" = "LOW" ]
  check "the same draft is LOW risk through Gmail" $?
  [ "$(py "$TMP/gmdraft.json" "d['preview']['provider']")" = "gmail" ]
  check "routing back to Gmail takes effect immediately" $?
else
  check "draft email fixture present" 1
fi

head_ "audit log is queryable and read-only (SPEC-003 §7)"
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/audit-log?limit=100" -o "$TMP/audit.json"
check "audit log endpoint responds" $?
[ "$(py "$TMP/audit.json" "d['total']")" -gt 0 ]
STATUS=$?
check "$(py "$TMP/audit.json" "d['total']") events recorded" "$STATUS"
[ "$(py "$TMP/audit.json" "len(d['facets']['events'])")" -gt 3 ]
check "event facets available for filtering" $?
for verb in POST PUT PATCH DELETE; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" -X $verb "$API_BASE/api/audit-log")
  [ "$CODE" = "405" ] || [ "$CODE" = "404" ]
  check "$verb on the audit log is not implemented (got $CODE)" $?
done
FIRST_EVENT=$(py "$TMP/audit.json" "d['entries'][0]['event']")
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/audit-log?event=$FIRST_EVENT" -o "$TMP/audit2.json"
[ "$(py "$TMP/audit2.json" "sum(1 for e in d['entries'] if e['event'] != '$FIRST_EVENT')")" = "0" ]
check "event filter returns only that event" $?
[ -n "$(py "$TMP/audit.json" "d['entries'][0]['actorLabel']")" ]
check "the actor is resolved to a readable label, not a raw id" $?
[ "$(py "$TMP/audit.json" "d.get('nextCursor', 'MISSING')")" != "MISSING" ]
check "cursor-based pagination is exposed for 'load more'" $?

head_ "every module route renders (SPEC-005 §2)"
for path in /dashboard /dashboard/action-items /dashboard/audit-log /dashboard/settings \
            /dashboard/settings/profile /dashboard/settings/credentials \
            /dashboard/settings/integrations /dashboard/modules/upload \
            /dashboard/modules/transcripts /dashboard/modules/live-meetings \
            /dashboard/modules/search /dashboard/modules/workflows /dashboard/modules/insights; do
  CODE=$(curl -s -o "$TMP/page.html" -w '%{http_code}' "$BASE$path")
  BROKE=1
  grep -qE 'Something broke on this page|Application error|Internal Server Error' "$TMP/page.html" && BROKE=0
  [ "$CODE" = "200" ] && [ "$BROKE" = "1" ]
  check "$path renders (got $CODE)" $?
done

head_ "theme tokens present for both themes"
curl -s "$BASE/dashboard" -o "$TMP/page.html"
grep -q 'data-theme' "$TMP/page.html"
check "theme init script inlined before paint" $?
grep -q 'Skip to content' "$TMP/page.html"
check "skip link present for keyboard users" $?

head_ "authentication (SPEC-006 §8)"
JAR="$TMP/auth-cookies.txt"
rm -f "$JAR"
SIGNUP_EMAIL="smoke-$$@acme.test"

# 1. policy is enforced server-side
CODE=$(curl -s -o "$TMP/su.json" -w '%{http_code}' -X POST -H 'content-type: application/json' \
  -d "{\"name\":\"Smoke Tester\",\"email\":\"$SIGNUP_EMAIL\",\"password\":\"short\"}" \
  "$BASE/api/auth/signup")
[ "$CODE" = "422" ] && has "$TMP/su.json" 'password_too_short'
check "sign-up rejects a weak password (got $CODE)" $?

# 2. a real account, and a real session
curl -s -c "$JAR" -o "$TMP/su2.json" -X POST -H 'content-type: application/json' \
  -d "{\"name\":\"Smoke Tester\",\"email\":\"$SIGNUP_EMAIL\",\"password\":\"eleven wooden lighthouses\"}" \
  "$BASE/api/auth/signup"
[ "$(py "$TMP/su2.json" "d['user']['email']")" = "$SIGNUP_EMAIL" ]
check "sign-up creates the account" $?
grep -q v2b_session "$JAR"
check "a session cookie is issued" $?
grep -q 'passwordHash' "$TMP/su2.json" && HLEAK=0 || HLEAK=1
[ "$HLEAK" = "1" ]
check "no password hash in the response (SPEC-006 §8.7)" $?

# 3. the session identifies THAT user, not the dev identity
# `/api/profile` moved to FastAPI (SPEC-015 §7); the dashboard's sidebar is the
# remaining Next.js surface that renders the session's identity, so it is what these
# two checks read instead.
curl -s -b "$JAR" -o "$TMP/me.html" "$BASE/dashboard"
grep -qF "$SIGNUP_EMAIL" "$TMP/me.html"
check "the session identifies its own user (SPEC-006 §8.1)" $?
curl -s -o "$TMP/nodev.html" "$BASE/dashboard"
grep -qF "$SIGNUP_EMAIL" "$TMP/nodev.html" && SAWSIGNUP=0 || SAWSIGNUP=1
[ "$SAWSIGNUP" = "1" ]
check "without the cookie, the dev identity answers instead" $?

# 4. sign-up on an existing address is refused
CODE=$(curl -s -o "$TMP/dup.json" -w '%{http_code}' -X POST -H 'content-type: application/json' \
  -d "{\"name\":\"Smoke Tester\",\"email\":\"$SIGNUP_EMAIL\",\"password\":\"eleven wooden lighthouses\"}" \
  "$BASE/api/auth/signup")
[ "$CODE" = "409" ] && has "$TMP/dup.json" 'account_exists'
check "a duplicate sign-up returns 409 (got $CODE)" $?

# 5. login is not an account enumerator
curl -s -o "$TMP/bad1.json" -w '%{http_code}' -X POST -H 'content-type: application/json' \
  -d "{\"email\":\"$SIGNUP_EMAIL\",\"password\":\"wrong password entirely\"}" \
  "$BASE/api/auth/login" > "$TMP/code1"
curl -s -o "$TMP/bad2.json" -w '%{http_code}' -X POST -H 'content-type: application/json' \
  -d '{"email":"definitely-nobody@nowhere.test","password":"wrong password entirely"}' \
  "$BASE/api/auth/login" > "$TMP/code2"
[ "$(cat "$TMP/code1")" = "$(cat "$TMP/code2")" ]
STATUS=$?
check "wrong password and unknown account share a status ($(cat "$TMP/code1"))" "$STATUS"
[ "$(py "$TMP/bad1.json" "d['error']['message']")" = "$(py "$TMP/bad2.json" "d['error']['message']")" ]
check "…and an identical message (SPEC-006 §8.3)" $?

# 6. rotating the password invalidates the session
# The mutation itself now goes through FastAPI's auth surface — mint this account a
# bearer token first (its Next.js session cookie cannot authenticate there).
curl -s -X POST "$API_BASE/api/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$SIGNUP_EMAIL\",\"password\":\"eleven wooden lighthouses\"}" -o "$TMP/signup-login.json"
SIGNUP_AUTH="authorization: Bearer $(py "$TMP/signup-login.json" "d['tokens']['accessToken']")"
curl -s -H "$SIGNUP_AUTH" -o /dev/null -X POST -H 'content-type: application/json' \
  -d '{"currentPassword":"eleven wooden lighthouses","newPassword":"twelve wooden lighthouses"}' \
  "$API_BASE/api/auth/change-password"
# `optional_user` returns null for a session whose `pwd` claim no longer matches (rather
# than falling through to the dev identity, which only applies when no cookie is sent at
# all — see the note in src/lib/auth.ts), so the dashboard layout redirects to /login.
CODE=$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/dashboard")
[ "$CODE" = "307" ]
check "the old cookie is rejected after rotation (got $CODE)" $?

CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' \
  -d "{\"email\":\"$SIGNUP_EMAIL\",\"password\":\"eleven wooden lighthouses\"}" "$BASE/api/auth/login")
[ "$CODE" = "401" ]
check "the old password no longer works (got $CODE)" $?

rm -f "$JAR"
curl -s -c "$JAR" -o /dev/null -X POST -H 'content-type: application/json' \
  -d "{\"email\":\"$SIGNUP_EMAIL\",\"password\":\"twelve wooden lighthouses\"}" "$BASE/api/auth/login"
curl -s -b "$JAR" -o "$TMP/me2.html" "$BASE/dashboard"
grep -qF "$SIGNUP_EMAIL" "$TMP/me2.html"
check "the new password issues a working session" $?

# 7. logout — the cookie is cleared outright, so the next request sends none at all
# and falls through to the dev identity, the same as "without the cookie" above.
curl -s -b "$JAR" -c "$JAR" -o /dev/null -X POST "$BASE/api/auth/logout"
curl -s -b "$JAR" -o "$TMP/out.html" "$BASE/dashboard"
grep -qF "$SIGNUP_EMAIL" "$TMP/out.html" && SAWSIGNUP=0 || SAWSIGNUP=1
[ "$SAWSIGNUP" = "1" ]
check "logout clears the session" $?

head_ "the session cookie survives the transport it is served over"
# The regression guard: a Secure cookie over http is silently dropped by strict
# clients, and the app then falls through to the development identity — showing a
# *different account's* name to someone who just signed in.
COOKIE_EMAIL="cookiecheck-$$@acme.test"
COOKIE_JAR="$TMP/cookie-jar.txt"
rm -f "$COOKIE_JAR"
curl -s -o /dev/null -X POST -H 'content-type: application/json' \
  -d "{\"name\":\"Cookie Check\",\"email\":\"$COOKIE_EMAIL\",\"password\":\"cookie throwaway phrase\"}" \
  "$BASE/api/auth/signup"
curl -s -D "$TMP/cookie-headers.txt" -c "$COOKIE_JAR" -o /dev/null -X POST \
  -H 'content-type: application/json' \
  -d "{\"email\":\"$COOKIE_EMAIL\",\"password\":\"cookie throwaway phrase\"}" \
  "$BASE/api/auth/login"

grep -qi '^set-cookie:.*v2b_session' "$TMP/cookie-headers.txt"
check "a session cookie is issued" $?
grep -qi '^set-cookie:.*HttpOnly' "$TMP/cookie-headers.txt"
check "…marked HttpOnly, so script cannot read it" $?
grep -qi '^set-cookie:.*SameSite=lax' "$TMP/cookie-headers.txt"
check "…with SameSite=lax" $?

# Secure must match the scheme the app is actually served over, not NODE_ENV.
case "$BASE" in
  https://*)
    grep -qi '^set-cookie:.*Secure' "$TMP/cookie-headers.txt"
    check "…and Secure, because this is https" $?
    ;;
  *)
    grep -qi '^set-cookie:.*Secure' "$TMP/cookie-headers.txt" && HASSECURE=0 || HASSECURE=1
    [ "$HASSECURE" = "1" ]
    check "…and NOT Secure, because this is plain http" $?
    ;;
esac

# The property that actually matters: the identity shown is the one signed in with.
curl -fsS -b "$COOKIE_JAR" "$BASE/dashboard" -o "$TMP/cookie-me.html"
grep -qF "$COOKIE_EMAIL" "$TMP/cookie-me.html"
check "the profile matches the account signed in with" $?
grep -qF "Cookie Check" "$TMP/cookie-me.html"
check "…including the display name" $?

# And without the cookie the dev identity is *announced*, not silently substituted.
curl -s "$BASE/dashboard" -o "$TMP/dev-dash.html"
grep -q 'You are not signed in' "$TMP/dev-dash.html"
check "an unauthenticated visit says so, rather than showing another account as yours" $?

head_ "auth screens (SPEC-006 §8.5, §8.6)"
for path in /login /signup; do
  CODE=$(curl -s -o "$TMP/auth.html" -w '%{http_code}' "$BASE$path")
  [ "$CODE" = "200" ]
  check "$path is reachable in development (got $CODE)" $?
  grep -q 'bi-eye' "$TMP/auth.html"
  check "$path has a show/hide password control" $?
  grep -q 'aria-describedby\|aria-invalid\|Work email' "$TMP/auth.html"
  check "$path wires errors to inputs for assistive tech" $?
done
grep -q 'auth-confirmPassword' "$TMP/auth.html"
check "sign-up confirms the password" $?

# Open-redirect protection: an absolute ?next= must never become a navigation
# target. The string itself does appear in Next's serialised searchParams — that is
# unavoidable and harmless — so the assertion is specifically that it is not an
# href, an action, or a location assignment.
curl -s -o "$TMP/redir.html" "$BASE/login?next=https://evil.example/steal"
grep -qE 'href=\\?"https://evil\.example|action=\\?"https://evil\.example|assign\(\\?"https://evil\.example' \
  "$TMP/redir.html" && OPENREDIR=0 || OPENREDIR=1
[ "$OPENREDIR" = "1" ]
check "an absolute ?next= never becomes a navigation target (SPEC-006 §8.6)" $?
# And a same-origin path is accepted, so the guard is not simply discarding everything.
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/login?next=/dashboard/audit-log")
[ "$CODE" = "200" ]
check "a same-origin ?next= is accepted (got $CODE)" $?

head_ "password reset (SPEC-007 §5)"
# Its own throwaway account, for the same reason as the password-policy block: this
# flow *changes a password*, and it must not change one belonging to a real person.
RESET_EMAIL="resettest-$$@acme.test"
curl -s -o /dev/null -X POST -H 'content-type: application/json' \
  -d "{\"name\":\"Reset Tester\",\"email\":\"$RESET_EMAIL\",\"password\":\"reset throwaway phrase\"}" \
  "$BASE/api/auth/signup"

# 1. enumeration resistance: identical body for a real and a fake address
curl -s -X POST -H 'content-type: application/json' -d "{\"email\":\"$RESET_EMAIL\"}" \
  "$BASE/api/auth/forgot-password" -o "$TMP/fp-real.json"
curl -s -X POST -H 'content-type: application/json' -d '{"email":"definitely-nobody@nowhere.test"}' \
  "$BASE/api/auth/forgot-password" -o "$TMP/fp-fake.json"
[ "$(py "$TMP/fp-real.json" "d['message']")" = "$(py "$TMP/fp-fake.json" "d['message']")" ]
check "forgot-password says the same thing for a real and an unknown address" $?
[ "$(py "$TMP/fp-real.json" "d['ok']")" = "True" ] && [ "$(py "$TMP/fp-fake.json" "d['ok']")" = "True" ]
check "…and returns ok for both" $?

# 2. the dev link is present only because this stack opts in
LINK=$(py "$TMP/fp-real.json" "d.get('devLink') or ''")
if [ -z "$LINK" ]; then
  dim "        (DEV_EXPOSE_RESET_LINK is off; skipping the reset flow)"
else
  check "a reset link is issued for a real account" 0
  TOKEN=$(printf '%s' "$LINK" | sed -n 's/.*token=\([^&]*\).*/\1/p')

  # 3. the token validates before it is spent
  curl -fsS "$BASE/api/auth/reset-password?token=$TOKEN" -o "$TMP/tok.json"
  [ "$(py "$TMP/tok.json" "d['valid']")" = "True" ]
  check "the token validates on load, before anything is typed" $?

  # 4. requesting again kills the previous token
  curl -s -X POST -H 'content-type: application/json' -d "{\"email\":\"$RESET_EMAIL\"}" \
    "$BASE/api/auth/forgot-password" -o "$TMP/fp2.json"
  TOKEN2=$(py "$TMP/fp2.json" "d.get('devLink') or ''" | sed -n 's/.*token=\([^&]*\).*/\1/p')
  curl -fsS "$BASE/api/auth/reset-password?token=$TOKEN" -o "$TMP/tok-old.json"
  [ "$(py "$TMP/tok-old.json" "d['valid']")" = "False" ]
  check "a new request invalidates the outstanding token" $?

  # 5. the password policy still applies
  CODE=$(curl -s -o "$TMP/rp-weak.json" -w '%{http_code}' -X POST -H 'content-type: application/json' \
    -d "{\"token\":\"$TOKEN2\",\"newPassword\":\"short\"}" "$BASE/api/auth/reset-password")
  [ "$CODE" = "422" ] && has "$TMP/rp-weak.json" 'password_too_short'
  check "a weak new password is refused (got $CODE)" $?

  # 6. a valid reset completes, signs in, and revokes prior sessions
  rm -f "$TMP/reset-jar.txt"
  curl -s -c "$TMP/reset-jar.txt" -X POST -H 'content-type: application/json' \
    -d "{\"token\":\"$TOKEN2\",\"newPassword\":\"nineteen copper kettles\"}" \
    "$BASE/api/auth/reset-password" -o "$TMP/rp-ok.json"
  [ "$(py "$TMP/rp-ok.json" "d['ok']")" = "True" ]
  check "a valid reset completes" $?
  grep -q v2b_session "$TMP/reset-jar.txt"
  check "…and signs the user in" $?
  curl -s -b "$TMP/reset-jar.txt" -o "$TMP/rp-me.html" "$BASE/dashboard"
  grep -qF "$RESET_EMAIL" "$TMP/rp-me.html"
  check "…as the right account" $?

  # 7. single use
  CODE=$(curl -s -o "$TMP/rp-replay.json" -w '%{http_code}' -X POST -H 'content-type: application/json' \
    -d "{\"token\":\"$TOKEN2\",\"newPassword\":\"twenty copper kettles\"}" "$BASE/api/auth/reset-password")
  [ "$CODE" = "400" ] && has "$TMP/rp-replay.json" 'invalid_reset_token'
  check "the token cannot be replayed (got $CODE)" $?

  # 8. the new password is the one that works
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'content-type: application/json' \
    -d "{\"email\":\"$RESET_EMAIL\",\"password\":\"nineteen copper kettles\"}" "$BASE/api/auth/login")
  [ "$CODE" = "200" ]
  check "the new password signs in (got $CODE)" $?

  # 9. tokens are at rest as hashes
  if command -v docker > /dev/null 2>&1; then
    STORED=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
      'SELECT "tokenHash" FROM "PasswordResetToken" ORDER BY "createdAt" DESC LIMIT 1;' 2>/dev/null | tr -d ' \r')
    printf '%s' "$STORED" | grep -qE '^[0-9a-f]{64}$'
    check "the token is stored as a SHA-256 hash" $?
    [ "$STORED" != "$TOKEN2" ]
    check "the plaintext token is not in the database" $?
  fi
fi

head_ "google sign-in degrades and appears correctly (SPEC-007 §5)"
GOOGLE_ON=$(py "$TMP/health.json" "d.get('googleSignInConfigured')")
curl -s "$BASE/login" -o "$TMP/login.html"
if [ "$GOOGLE_ON" = "True" ]; then
  grep -q 'Continue with Google' "$TMP/login.html"
  check "the button is shown when the OAuth app is configured" $?
  LOC=$(curl -s -o /dev/null -D - "$BASE/api/auth/google" 2>/dev/null | grep -i '^location' | tr -d '\r')
  case "$LOC" in *accounts.google.com*code_challenge*) OK=0 ;; *) OK=1 ;; esac
  [ "$OK" = "0" ]
  check "the route redirects to Google with PKCE" $?
else
  grep -q 'Continue with Google' "$TMP/login.html" && SHOWN=0 || SHOWN=1
  [ "$SHOWN" = "1" ]
  check "the button is hidden when Google is unconfigured" $?
  LOC=$(curl -s -o /dev/null -D - "$BASE/api/auth/google" 2>/dev/null | grep -i '^location' | tr -d '\r')
  case "$LOC" in *"/login?error="*) OK=0 ;; *) OK=1 ;; esac
  [ "$OK" = "0" ]
  check "…and the route redirects back with a readable reason" $?
fi

head_ "recovery pages render"
for path in /forgot-password /reset-password; do
  CODE=$(curl -s -o "$TMP/rec.html" -w '%{http_code}' "$BASE$path")
  [ "$CODE" = "200" ]
  check "$path renders (got $CODE)" $?
done
grep -q 'Forgotten your password' "$TMP/login.html"
check "/login offers a route to recovery" $?
curl -s "$BASE/reset-password?token=obviously-not-a-real-token-at-all" -o "$TMP/badtok.html"
grep -q 'not valid\|expired\|already been used' "$TMP/badtok.html"
check "an invalid token explains itself instead of erroring" $?

head_ "welcome email on registration (SPEC-007 §5)"
WELCOME_EMAIL="welcome-$$@acme.test"
curl -s -X POST -H 'content-type: application/json' \
  -d "{\"name\":\"Welcome Tester\",\"email\":\"$WELCOME_EMAIL\",\"password\":\"eleven wooden lighthouses\"}" \
  "$BASE/api/auth/signup" -o "$TMP/welcome.json"
[ "$(py "$TMP/welcome.json" "d['user']['email']")" = "$WELCOME_EMAIL" ]
check "sign-up succeeds" $?
if command -v docker > /dev/null 2>&1; then
  docker compose logs web 2>/dev/null | grep -q 'auth.welcome_email'
  check "a welcome email was dispatched" $?
  docker compose logs web 2>/dev/null | grep -q 'auth.welcome_email_failed' && FAILED=0 || FAILED=1
  [ "$FAILED" = "1" ]
  check "…without failing the registration" $?
fi

head_ "ingest pipeline (SPEC-010 §10)"

# Build a genuinely valid WAV: validation reads magic bytes, so a fake would be refused
# — which is itself one of the things under test below.
python3 - <<'WAVGEN'
import struct, math, wave, os
with wave.open('/tmp/v2b-smoke.wav', 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    frames = bytearray()
    for i in range(16000 * 2):
        frames += struct.pack('<h', int(2500 * math.sin(2 * math.pi * 220 * i / 16000)))
    w.writeframes(bytes(frames))
WAVGEN

curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/transcripts/pipeline-status" -o "$TMP/pipe.json"
check "pipeline status endpoint responds" $?
[ "$(py "$TMP/pipe.json" "d['sampleAvailable']")" = "True" ]
check "the bundled sample is always available, so the pipeline is demonstrable" $?
[ "$(py "$TMP/pipe.json" "len([c for c in d['extract']['candidates'] if c['freeTier']])")" -ge 3 ]
check "at least three free extraction providers are offered" $?

# ── a renamed non-media file must be refused on its bytes
printf '%%PDF-1.7\n%%not audio at all\n' > "$TMP/fake.mp3"
CODE=$(curl -s -o "$TMP/fake.json" -w '%{http_code}' -X POST -H "$AUTH_HEADER" -F "file=@$TMP/fake.mp3" "$API_BASE/api/transcripts")
[ "$CODE" = "422" ] && has "$TMP/fake.json" 'unsupported_media'
check "a PDF renamed .mp3 is refused on its magic bytes (got $CODE)" $?

# ── a real upload is accepted and processed
CODE=$(curl -s -o "$TMP/upload.json" -w '%{http_code}' -X POST -H "$AUTH_HEADER" \
  -F "file=@/tmp/v2b-smoke.wav;filename=Smoke meeting.wav" "$API_BASE/api/transcripts")
[ "$CODE" = "202" ]
check "a valid recording is accepted with 202, not held open (got $CODE)" $?
TRANSCRIPT_ID=$(py "$TMP/upload.json" "d['transcript']['id']")
[ -n "$TRANSCRIPT_ID" ]
check "the response carries the transcript id to poll" $?

# ── poll to completion
TRIES=0
while [ $TRIES -lt 60 ]; do
  curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID" -o "$TMP/tr.json" 2>/dev/null || true
  [ "$(py "$TMP/tr.json" "d['stage']")" = "done" ] && break
  TRIES=$((TRIES + 1)); sleep 1
done
[ "$(py "$TMP/tr.json" "d['stage']")" = "done" ]
check "the pipeline reaches done by polling (${TRIES}s)" $?
[ "$(py "$TMP/tr.json" "d['status']")" = "READY" ]
check "the transcript is READY" $?
[ "$(py "$TMP/tr.json" "d['counts']['segments']")" -gt 5 ]
STATUS=$?
check "$(py "$TMP/tr.json" "d['counts']['segments']") segments were produced" "$STATUS"
[ "$(py "$TMP/tr.json" "sum(len(s['words']) for s in d['segments'])")" -gt 50 ]
check "$(py "$TMP/tr.json" "sum(len(s['words']) for s in d['segments'])") word-level timings persisted" $?
[ "$(py "$TMP/tr.json" "d['counts']['actionItems']")" -gt 0 ]
STATUS=$?
check "$(py "$TMP/tr.json" "d['counts']['actionItems']") action items extracted with no API key" "$STATUS"
[ "$(py "$TMP/tr.json" "d['counts']['decisions']")" -gt 0 ]
check "decisions were extracted too" $?
[ -n "$(py "$TMP/tr.json" "d['summary'] or ''")" ]
check "a summary was produced" $?

# ── the fixture is labelled as a fixture rather than passing for a model
[ "$(py "$TMP/tr.json" "d['transcribeProvider']")" = "sample" ]
check "the transcription provider is recorded honestly as 'sample'" $?

# ── extracted items behave exactly like seeded ones
curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items?transcriptId=$TRANSCRIPT_ID&limit=100" -o "$TMP/extracted.json"
[ "$(py "$TMP/extracted.json" "d['counts']['total']")" -gt 0 ]
check "extracted items appear on the board" $?
[ "$(py "$TMP/extracted.json" "sum(1 for i in d['items'] if i['sourceQuote'])")" = "$(py "$TMP/extracted.json" "d['counts']['total']")" ]
check "every extracted item cites a source quote (SPEC-000 §5)" $?
[ "$(py "$TMP/extracted.json" "sum(1 for i in d['items'] if i['sourceTimestampLabel'])")" = "$(py "$TMP/extracted.json" "d['counts']['total']")" ]
check "…and a source timestamp" $?
[ "$(py "$TMP/extracted.json" "sum(1 for i in d['items'] if i.get('supersededBy'))")" -ge 1 ]
check "a superseded pair was detected and linked" $?
[ "$(py "$TMP/extracted.json" "sum(1 for i in d['items'] if i['confidence'] == 'LOW')")" -ge 1 ]
check "hedged language was reported as LOW confidence" $?
[ "$(py "$TMP/extracted.json" "sum(1 for i in d['items'] if i['readiness'] == 'NEEDS_CLARIFICATION')")" -ge 1 ]
check "incomplete items landed in the clarification lane, not the ready one" $?
[ "$(py "$TMP/extracted.json" "sum(1 for i in d['items'] if i['actionType'] == 'CALENDAR' and 'startsAt' in i['payload'])")" = "0" ]
check "no calendar time was invented for a meeting nobody scheduled (SPEC-010 §10.7)" $?

# ── audio streams, including a range request for seeking
curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID/audio" > "$TMP/audio-code"
[ "$(cat "$TMP/audio-code")" = "200" ]
STATUS=$?
check "the stored audio streams back (got $(cat "$TMP/audio-code"))" "$STATUS"
RANGE_CODE=$(curl -s -o /dev/null -H "$AUTH_HEADER" -H 'Range: bytes=0-1023' -w '%{http_code}' "$API_BASE/api/transcripts/$TRANSCRIPT_ID/audio")
[ "$RANGE_CODE" = "206" ]
check "a Range request returns 206, so the player can seek (got $RANGE_CODE)" $?

# ── identical bytes must not spend quota twice
CODE=$(curl -s -o "$TMP/reupload.json" -w '%{http_code}' -X POST -H "$AUTH_HEADER" \
  -F "file=@/tmp/v2b-smoke.wav;filename=Smoke meeting.wav" "$API_BASE/api/transcripts")
[ "$CODE" = "200" ] && [ "$(py "$TMP/reupload.json" "d['transcript']['reused']")" = "True" ]
check "an identical re-upload is reused, not reprocessed (got $CODE)" $?
[ "$(py "$TMP/reupload.json" "d['transcript']['id']")" = "$TRANSCRIPT_ID" ]
check "…and returns the same transcript" $?

# ── re-extraction preserves human decisions (SPEC-010 §8)
FIRST_ID=$(py "$TMP/extracted.json" "d['items'][0]['id']")
curl -fsS -X PATCH -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"status":"APPROVED"}' \
  "$API_BASE/api/action-items/$FIRST_ID" -o /dev/null
curl -fsS -X POST -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID/extract" -o "$TMP/reextract.json"
[ "$(py "$TMP/reextract.json" "d['ok']")" = "True" ]
check "re-extraction succeeds" $?
CODE=$(curl -s -o "$TMP/kept.json" -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/action-items/$FIRST_ID")
[ "$CODE" = "200" ] && [ "$(py "$TMP/kept.json" "d['status']")" = "APPROVED" ]
check "an approved item survives re-extraction (SPEC-010 §10.9)" $?

# ── a failed extraction is presented safely, and is recoverable (SPEC-010 §3.4)
#
# Scope, stated plainly: the *classification* of a Groq 404 into `model_unavailable` is
# covered by tests/extraction-errors.test.ts, which stubs fetch and asserts on the error
# object. It cannot be forced from out here without a fake Groq. What this block proves is
# everything downstream of that classification, against the running app and a real row:
# what reaches the screen, what survives, and that retry works. The failure state is
# written directly to the row, which is exactly what `extractInto` writes.
Q() { docker compose exec -T db psql -U vowcraft -d vowcraft -tAc "$1" < /dev/null 2>/dev/null | tr -d ' \r'; }

SEGMENTS_BEFORE=$(Q "SELECT count(*) FROM \"Segment\" WHERE \"transcriptId\"='$TRANSCRIPT_ID';")
ITEMS_BEFORE=$(Q "SELECT count(*) FROM \"ActionItem\" WHERE \"transcriptId\"='$TRANSCRIPT_ID';")
SAFE_MSG='The extraction model is unavailable. Please check the configured Groq model and try again.'
Q "UPDATE \"Transcript\" SET \"extractError\"='$SAFE_MSG', \"extractErrorCode\"='model_unavailable', \"extractModel\"='openai/gpt-oss-120b' WHERE id='$TRANSCRIPT_ID';" > /dev/null

CODE=$(curl -s -o "$TMP/failed-extract.json" -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID")
STATUS=$?
[ "$CODE" = "200" ] && [ "$STATUS" = "0" ]
check "a transcript with a failed extraction is still served (got $CODE)" $?

[ "$(py "$TMP/failed-extract.json" "d['status']")" = "READY" ]
check "…and is still READY, so it stays readable and exportable" $?

SEGMENTS_AFTER=$(Q "SELECT count(*) FROM \"Segment\" WHERE \"transcriptId\"='$TRANSCRIPT_ID';")
ITEMS_AFTER=$(Q "SELECT count(*) FROM \"ActionItem\" WHERE \"transcriptId\"='$TRANSCRIPT_ID';")
[ "$SEGMENTS_AFTER" = "$SEGMENTS_BEFORE" ] && [ "$SEGMENTS_AFTER" != "0" ]
check "its segments are not a casualty of the failure ($SEGMENTS_BEFORE segments)" $?

CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID/export?format=srt")
[ "$CODE" = "200" ]
check "…and it still exports as SRT (got $CODE)" $?

CODE=$(curl -s -o "$TMP/failed-page.html" -w '%{http_code}' "$BASE/dashboard/transcripts")
[ "$CODE" = "200" ]
check "the recordings page renders with a failed extraction on it (got $CODE)" $?

grep -qF "$SAFE_MSG" "$TMP/failed-page.html"
check "the written sentence is what the user sees" $?

grep -qF 'Retry extraction' "$TMP/failed-page.html"
check "a retry affordance is on the row, not only in the upload panel" $?

grep -qF 'openai/gpt-oss-120b' "$TMP/failed-page.html"
check "the configured model is named, so the user knows what to change" $?

# The provider's own vocabulary must not appear anywhere on the page.
LEAKED=""
for TERM in model_not_found invalid_request_error 'do not have access' 'HTTP 404' gsk_; do
  grep -qF "$TERM" "$TMP/failed-page.html" && LEAKED="$LEAKED $TERM"
done
[ -z "$LEAKED" ]
check "no provider error text or key reaches the page" $? "leaked:$LEAKED"

# "A provider is configured now, retrying will work" is true for a missing key and false
# for an unusable model. Showing it here would send the user round a loop.
grep -qF 'is configured now' "$TMP/failed-page.html"
NOT_HINTED=$?
[ "$NOT_HINTED" != "0" ]
check "the 'configured now, retry will work' hint is suppressed for a bad model" $?

grep -qF 'Retrying unchanged returns the same error' "$TMP/failed-page.html"
check "…and replaced by what actually has to change" $?

# Retry must answer, not 500, and must leave the transcript alone whatever it decides.
CODE=$(curl -s -o "$TMP/retry.json" -w '%{http_code}' -X POST -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID/extract")
[ "$CODE" = "200" ]
check "retry answers 200 with a handled result rather than an error page (got $CODE)" $?

RETRY_OK=$(py "$TMP/retry.json" "d['ok']")
[ -n "$RETRY_OK" ]
check "…and its body is readable JSON reporting ok=$RETRY_OK" $?

SEGMENTS_RETRY=$(Q "SELECT count(*) FROM \"Segment\" WHERE \"transcriptId\"='$TRANSCRIPT_ID';")
[ "$SEGMENTS_RETRY" = "$SEGMENTS_BEFORE" ]
check "…and the transcript is intact after the retry too" $?

ITEMS_RETRY=$(Q "SELECT count(*) FROM \"ActionItem\" WHERE \"transcriptId\"='$TRANSCRIPT_ID' AND status <> 'PROPOSED';")
[ "$ITEMS_RETRY" != "0" ]
check "…and the item a human approved is still approved ($ITEMS_RETRY decided)" $?

# The audit trail records extraction attempts on both outcomes with the model named.
EXTRACT_AUDITS=$(Q "SELECT count(*) FROM \"AuditLog\" WHERE event IN ('transcript.extracted','transcript.extraction_failed') AND metadata->>'transcriptId'='$TRANSCRIPT_ID';")
[ "${EXTRACT_AUDITS:-0}" -gt 0 ]
check "extraction attempts are in the audit log ($EXTRACT_AUDITS for this transcript)" $?

MODELLED=$(Q "SELECT count(*) FROM \"AuditLog\" WHERE event IN ('transcript.extracted','transcript.extraction_failed') AND metadata->>'transcriptId'='$TRANSCRIPT_ID' AND metadata->>'model' IS NOT NULL;")
[ "${MODELLED:-0}" -gt 0 ]
check "…each naming the model it used ($MODELLED of $EXTRACT_AUDITS)" $?

STATUSED=$(Q "SELECT count(*) FROM \"AuditLog\" WHERE event IN ('transcript.extracted','transcript.extraction_failed') AND metadata->>'transcriptId'='$TRANSCRIPT_ID' AND metadata->>'extractionStatus' IN ('SUCCEEDED','FAILED');")
[ "${STATUSED:-0}" -gt 0 ]
check "…and its extraction status, so the two outcomes are one query apart" $?

KEYLEAK=$(Q "SELECT count(*) FROM \"AuditLog\" WHERE metadata::text LIKE '%gsk\\_%' OR metadata::text LIKE '%model_not_found%';")
[ "${KEYLEAK:-1}" = "0" ]
check "the audit log stores no provider body and no key" $?

# ── deletion cascades
curl -fsS -X DELETE -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID" -o /dev/null
check "the transcript deletes" $?
CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID")
[ "$CODE" = "404" ]
check "…and is gone (got $CODE)" $?
CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$TRANSCRIPT_ID/audio")
[ "$CODE" = "404" ]
check "…along with its stored audio" $?

head_ "video upload (SPEC-010 §4.1)"
VIDEO_OK=$(py "$TMP/health.json" "d.get('videoUploadSupported')")
if [ "$VIDEO_OK" != "True" ]; then
  dim "        (FFmpeg is not available; skipping the video path)"
else
  check "FFmpeg is available, so video can be demuxed" 0

  # Real containers with a real audio track, generated by the container's own ffmpeg.
  docker compose exec -T web sh -c '
    ffmpeg -y -loglevel error -f lavfi -i testsrc=size=320x240:rate=15:duration=3 \
      -f lavfi -i "sine=frequency=440:duration=3" -c:v libx264 -preset ultrafast -c:a aac /tmp/c.mp4 &&
    ffmpeg -y -loglevel error -i /tmp/c.mp4 -c copy /tmp/c.mov &&
    ffmpeg -y -loglevel error -f lavfi -i testsrc=size=320x240:rate=15:duration=2 -c:v libx264 -preset ultrafast /tmp/mute.mp4
  ' > /dev/null 2>&1
  docker compose cp web:/tmp/c.mov "$TMP/clip.mov" > /dev/null 2>&1
  docker compose cp web:/tmp/mute.mp4 "$TMP/mute.mp4" > /dev/null 2>&1
  [ -s "$TMP/clip.mov" ]
  check "a test QuickTime file was produced" $?

  # ── the case that used to break: MOV is rejected by every provider's audio endpoint
  CODE=$(curl -s -o "$TMP/mov.json" -w '%{http_code}' -X POST -H "$AUTH_HEADER" \
    -F "file=@$TMP/clip.mov;filename=Screen recording.mov" "$API_BASE/api/transcripts")
  [ "$CODE" = "202" ]
  check "a QuickTime upload is accepted (got $CODE)" $?
  MOV_ID=$(py "$TMP/mov.json" "d['transcript']['id']")

  TRIES=0
  while [ $TRIES -lt 90 ]; do
    curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$MOV_ID" -o "$TMP/mov-tr.json" 2>/dev/null || true
    [ "$(py "$TMP/mov-tr.json" "d['stage']")" = "done" ] && break
    TRIES=$((TRIES + 1)); sleep 1
  done
  [ "$(py "$TMP/mov-tr.json" "d['status']")" = "READY" ]
  check "…and transcribes, rather than failing at the provider (${TRIES}s)" $?
  [ "$(py "$TMP/mov-tr.json" "d['counts']['segments']")" -gt 0 ]
  check "…producing segments" $?
  # FFmpeg read the container, so the duration is the real one rather than a guess.
  [ "$(py "$TMP/mov-tr.json" "d['durationMs'] or 0")" -gt 0 ]
  STATUS=$?
  check "the duration was read from the container ($(py "$TMP/mov-tr.json" "d['durationMs']")ms)" "$STATUS"

  # The ingest pipeline now runs in the FastAPI backend (SPEC-015 §7), not the Next.js
  # server, so this log line is emitted by the `api` container — whose structured logger
  # also, unlike the old one, puts a space after each JSON key's colon.
  docker compose logs api 2>/dev/null | grep -q '"container": "mov"'
  check "the audio track was extracted before anything was sent" $?
  docker compose logs api 2>/dev/null | grep -q '"hadVideo": true'
  check "…and the video track was discarded" $?

  # ── a video with no audio must say so, not fail opaquely
  if [ -s "$TMP/mute.mp4" ]; then
    curl -s -o "$TMP/mute.json" -X POST -H "$AUTH_HEADER" -F "file=@$TMP/mute.mp4;filename=No audio.mp4" \
      "$API_BASE/api/transcripts" > /dev/null
    MUTE_ID=$(py "$TMP/mute.json" "d['transcript']['id']")
    TRIES=0
    while [ $TRIES -lt 60 ]; do
      curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$MUTE_ID" -o "$TMP/mute-tr.json" 2>/dev/null || true
      [ "$(py "$TMP/mute-tr.json" "d['stage']")" = "done" ] && break
      TRIES=$((TRIES + 1)); sleep 1
    done
    [ "$(py "$TMP/mute-tr.json" "d['status']")" = "FAILED" ]
    check "a video with no audio track fails" $?
    grep -q 'no audio track' "$TMP/mute-tr.json"
    check "…and says exactly that, rather than a provider error" $?
  fi
fi

head_ "transcript reader (SPEC-012 §9)"
# Its own upload: the ingest section deletes the transcript it made, to prove deletion
# cascades. Reusing that id read a deleted row and produced eleven 404s that looked like
# reader bugs.
curl -s -o "$TMP/reader-upload.json" -X POST -H "$AUTH_HEADER" \
  -F "file=@/tmp/v2b-smoke.wav;filename=Reader meeting.wav" "$API_BASE/api/transcripts" > /dev/null
READER_ID=$(py "$TMP/reader-upload.json" "d['transcript']['id']")
TRIES=0
while [ $TRIES -lt 60 ]; do
  curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$READER_ID" -o "$TMP/tr.json" 2>/dev/null || true
  [ "$(py "$TMP/tr.json" "d['stage']")" = "done" ] && break
  TRIES=$((TRIES + 1)); sleep 1
done

if [ -z "$READER_ID" ]; then
  dim "        (upload failed; skipped)"
else
  CODE=$(curl -s -o "$TMP/reader.html" -w '%{http_code}' "$BASE/dashboard/transcripts/$READER_ID")
  [ "$CODE" = "200" ]
  check "the reader renders (got $CODE)" $?

  # Every word is addressable, and the count matches what the database holds.
  RENDERED=$(grep -o 'data-w=' "$TMP/reader.html" | wc -l | tr -d ' ')
  STORED=$(py "$TMP/tr.json" "sum(len(s['words']) for s in d['segments'])")
  [ "$RENDERED" = "$STORED" ]
  check "every stored word is rendered ($RENDERED of $STORED)" $?

  grep -q 'is-spoken' "$TMP/reader.html"
  check "the highlight class is defined" $?
  # The audio element mounts only once the browser has fetched it through the
  # authenticated client and turned it into an object URL (the cross-origin split
  # in SPEC-015 §7 means a bearer token cannot ride a plain `<audio src>`), so it is
  # absent from the server-rendered HTML curl sees. Assert the same endpoint directly.
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$READER_ID/audio")
  [ "$CODE" = "200" ]
  check "this transcript's audio streams back too (got $CODE)" $?
  RANGE_CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" -H 'Range: bytes=0-1023' "$API_BASE/api/transcripts/$READER_ID/audio")
  [ "$RANGE_CODE" = "206" ]
  check "…and a Range request against it returns 206" $?
  grep -q 'Find in transcript' "$TMP/reader.html"
  check "search is available" $?
  grep -q 'Following' "$TMP/reader.html"
  check "follow mode has a control" $?

  # Opening at a cited moment must not error, whatever the parameter.
  for T in 12500 0 999999999 notanumber -5; do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/dashboard/transcripts/$READER_ID?t=$T")
    [ "$CODE" = "200" ]
    check "?t=$T opens the reader (got $CODE)" $?
  done

  head_ "transcript export (SPEC-012 §6)"
  for FORMAT in txt md srt vtt; do
    CODE=$(curl -s -o "$TMP/export.$FORMAT" -D "$TMP/export-h.$FORMAT" -w '%{http_code}' \
      -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$READER_ID/export?format=$FORMAT")
    [ "$CODE" = "200" ] && [ -s "$TMP/export.$FORMAT" ]
    STATUS=$?
    check "$FORMAT exports (got $CODE, $(wc -c < "$TMP/export.$FORMAT" | tr -d ' ') bytes)" "$STATUS"
    grep -qi 'content-disposition:.*attachment' "$TMP/export-h.$FORMAT"
    check "…as a download rather than inline" $?
  done

  # The one thing SRT must get right: timings that match the stored boundaries.
  FIRST_START=$(py "$TMP/tr.json" "d['segments'][0]['startMs']")
  EXPECTED=$(python3 -c "
ms = int('$FIRST_START')
print('%02d:%02d:%02d,%03d' % (ms//3600000, (ms%3600000)//60000, (ms%60000)//1000, ms%1000))")
  grep -q "$EXPECTED" "$TMP/export.srt"
  check "SRT timings match the stored segment boundaries ($EXPECTED)" $?
  head -1 "$TMP/export.vtt" | grep -q '^WEBVTT'
  check "VTT carries its required signature" $?

  CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$READER_ID/export?format=pdf")
  [ "$CODE" = "400" ]
  check "an unsupported format is refused rather than silently wrong (got $CODE)" $?

  head_ "speaker renaming (SPEC-012 §9.6)"
  SPEAKER_ID=$(py "$TMP/tr.json" "(d['speakers'][0]['id'] if d.get('speakers') else '')")
  if [ -z "$SPEAKER_ID" ]; then
    dim "        (this transcript is unattributed, as Whisper output is; skipped)"
  else
    curl -fsS -X PATCH -H "$AUTH_HEADER" -H 'content-type: application/json' \
      -d "{\"speakers\":[{\"id\":\"$SPEAKER_ID\",\"displayName\":\"Marcus Vale\"}]}" \
      "$API_BASE/api/transcripts/$READER_ID/speakers" -o "$TMP/renamed.json"
    [ "$(py "$TMP/renamed.json" "next(s['displayName'] for s in d['speakers'] if s['id']=='$SPEAKER_ID')")" = "Marcus Vale" ]
    check "a speaker can be renamed" $?

    curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/transcripts/$READER_ID" -o "$TMP/tr-renamed.json"
    [ "$(py "$TMP/tr-renamed.json" "sum(1 for s in d['segments'] if s['speakerLabel'] == 'Marcus Vale')")" -gt 0 ]
    check "…and it applies to every segment they spoke" $?
    # The machine's original attribution must survive, so a re-transcription can match it.
    [ "$(py "$TMP/tr-renamed.json" "next(s['label'] for s in d['speakers'] if s['id']=='$SPEAKER_ID')")" != "Marcus Vale" ]
    check "…while the original label is preserved" $?

    # An id from another transcript must not be renameable through this one.
    CODE=$(curl -s -o "$TMP/badspk.json" -w '%{http_code}' -X PATCH -H "$AUTH_HEADER" -H 'content-type: application/json' \
      -d '{"speakers":[{"id":"clearly-not-a-real-id","displayName":"Nobody"}]}' \
      "$API_BASE/api/transcripts/$READER_ID/speakers")
    [ "$CODE" = "422" ] && has "$TMP/badspk.json" 'unknown_speaker'
    check "a foreign speaker id is refused (got $CODE)" $?
  fi

  head_ "the grounding loop (SPEC-012 §7)"
  curl -fsS -H "$AUTH_HEADER" "$API_BASE/api/action-items?transcriptId=$READER_ID&limit=50" -o "$TMP/grounded.json"
  CITED=$(py "$TMP/grounded.json" "sum(1 for i in d['items'] if i['sourceTimestampMs'] is not None and i['transcript'])")
  [ "${CITED:-0}" -gt 0 ]
  check "$CITED extracted items carry a citation that can be opened" $?
  curl -s -o "$TMP/cards.html" "$BASE/dashboard/action-items?transcriptId=$READER_ID"
  grep -q "/dashboard/transcripts/$READER_ID?t=" "$TMP/cards.html"
  check "the board links each citation into the reader at that moment" $?
  # The "hear this" link lives inside the collapsed source-quote disclosure, so it is
  # not in the server HTML — asserting on it tested the disclosure, not the feature.
  # The citation link itself is server-rendered, so assert it carries the exact offset.
  FIRST_TS=$(py "$TMP/grounded.json" "next(i['sourceTimestampMs'] for i in d['items'] if i['sourceTimestampMs'] is not None)")
  grep -q "?t=$FIRST_TS" "$TMP/cards.html"
  STATUS=$?
  check "…at the exact millisecond it cites (t=$FIRST_TS)" "$STATUS"
fi

head_ "the recordings page renders"
CODE=$(curl -s -o "$TMP/rec-page.html" -w '%{http_code}' "$BASE/dashboard/transcripts")
[ "$CODE" = "200" ]
check "/dashboard/transcripts renders (got $CODE)" $?
grep -q 'Drop a recording here' "$TMP/rec-page.html"
check "…with an upload affordance" $?

head_ "voice to BRD (SPEC-014) — served entirely by the FastAPI backend"
#
# Scope: everything reachable without a streaming-ASR key or an LLM key, which is what CI
# has. The document contract, id stability and refinement semantics are covered by
# tests/brd.test.ts and apps/api/tests/test_brd_contract.py; the create/refine round trip
# against a real database is covered by a stubbed-provider harness on each side. What this
# block proves is the surface: routes resolve, the unavailable state is honest, and no
# fabricated transcript is ever offered.
#
# Speech has no Next.js route any more — it moved to the backend outright, not just its
# implementation — so this authenticates against $API_BASE directly with a throwaway
# account, the same fixture pattern the rest of this suite uses. `optionalUser`'s
# dev-identity fallback was deliberately not ported (SPEC-006 §3), so the seeded demo
# account — which has no password — cannot sign in here; a real account is required.

CODE=$(curl -s -o "$TMP/api-health.json" -w '%{http_code}' "$API_BASE/health")
[ "$CODE" = "200" ]
check "the FastAPI backend is reachable at \$API_BASE (got $CODE)" $?

SPEECH_EMAIL="speechcheck-$$@acme.test"
curl -fsS -X POST -H 'content-type: application/json' \
  -d "{\"email\":\"$SPEECH_EMAIL\",\"password\":\"a memorable phrase you will recall\",\"name\":\"Speech Check\"}" \
  "$API_BASE/api/auth/signup" -o "$TMP/speech-signup.json"
SPEECH_TOKEN=$(py "$TMP/speech-signup.json" "d['tokens']['accessToken']")
[ -n "$SPEECH_TOKEN" ]
check "signup against the backend issues a bearer token" $?

CODE=$(curl -s -o "$TMP/speech-status.json" -w '%{http_code}' -H "authorization: Bearer $SPEECH_TOKEN" "$API_BASE/api/speech/status")
[ "$CODE" = "200" ]
check "speech status responds (got $CODE)" $?

SPEECH_OK=$(py "$TMP/speech-status.json" "d['available']")
[ -n "$SPEECH_OK" ]
check "…reporting availability explicitly (available=$SPEECH_OK)" $?

# Every provider must account for itself, available or not — a silent provider is one the
# user cannot fix.
UNREASONED=$(py "$TMP/speech-status.json" "len([c for c in d['candidates'] if not c['available'] and not c['reason']])")
[ "${UNREASONED:-1}" = "0" ]
check "every unavailable provider states a reason ($UNREASONED silent)" $?

CANDIDATES=$(py "$TMP/speech-status.json" "len(d['candidates'])")
[ "${CANDIDATES:-0}" -ge 2 ]
check "more than one ASR provider is registered ($CANDIDATES) — the layer is not single-vendor" $?

SELFHOSTED=$(py "$TMP/speech-status.json" "len([c for c in d['candidates'] if c['selfHosted']])")
[ "${SELFHOSTED:-0}" -ge 1 ]
check "…including a self-hosted option, so audio need not leave the network" $?

CODE=$(curl -s -o /dev/null -w '%{http_code}' -X GET "$API_BASE/api/speech/status")
[ "$CODE" = "401" ]
check "…and refuses an unauthenticated caller (got $CODE)" $?

if [ "$SPEECH_OK" = "False" ]; then
  # The honest-unavailable path. With a key present this block is skipped rather than
  # asserted the other way round, because the interesting claim is about the absence.
  # There is no /api/speech/session any more — a session is now the WS handshake itself
  # (SPEC-014 §3), so the closest equivalent check is that the handshake, once accepted,
  # answers with an explanatory error frame rather than silently closing. curl cannot speak
  # WebSocket, so that path is covered by apps/api/tests instead; here we confirm the HTTP
  # side of the same fact.
  MSG=$(py "$TMP/speech-status.json" "d['reason']")
  printf '%s' "$MSG" | grep -qi "key"
  check "…and the refusal says what to do about it" $?
fi

CODE=$(curl -s -o "$TMP/voice-page.html" -w '%{http_code}' "$BASE/dashboard")
[ "$CODE" = "200" ]
check "the landing page renders (got $CODE)" $?

# The landing route is the voice surface, not the analytics overview — SPEC-014 §2.
#
# Speech status now depends on a bearer token held in the browser (SPEC-014 §3), which a
# server render cannot see — the page always server-renders "Checking transcription…" and
# resolves the real state (a record button, or a stated reason it is unavailable) from
# $API_BASE after the browser's JS runs. curl cannot execute that, so the three states this
# used to distinguish are covered where they can actually run: the "unavailable" sentence
# and its reason are asserted against $API_BASE above; the button and the loading copy are
# asserted here, against what a server render genuinely produces.
grep -qF 'Checking transcription' "$TMP/voice-page.html"
check "the voice surface renders its loading state on first paint" $?

grep -qF "$API_BASE" "$TMP/voice-page.html" || true
BUNDLE_HAS_API=1
for CHUNK in $(grep -o '/_next/static/chunks/[a-zA-Z0-9._/-]*\.js' "$TMP/voice-page.html" | sort -u | head -40); do
  if curl -s "$BASE$CHUNK" | grep -qF "${API_BASE#http://}"; then BUNDLE_HAS_API=0; break; fi
done
[ "$BUNDLE_HAS_API" = "0" ]
check "…and the client bundle knows where the backend actually is" $?

# No text input: this is a voice surface, and a textarea would make the ASR dead weight.
grep -qF '<textarea' "$TMP/voice-page.html"
NOTEXTAREA=$?
[ "$NOTEXTAREA" != "0" ]
check "the voice surface offers no text box (SPEC-014 §2)" $?

CODE=$(curl -s -o "$TMP/overview-page.html" -w '%{http_code}' "$BASE/dashboard/overview")
[ "$CODE" = "200" ]
check "the analytics overview still resolves at its own route (got $CODE)" $?
grep -qiF 'Welcome back' "$TMP/overview-page.html"
check "…and still renders its content" $?

CODE=$(curl -s -o "$TMP/brd-list.html" -w '%{http_code}' "$BASE/dashboard/brd")
[ "$CODE" = "200" ]
check "the requirements-document history renders (got $CODE)" $?

CODE=$(curl -s -o "$TMP/brd-api.json" -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/brd")
[ "$CODE" = "200" ]
check "GET /api/brd responds (got $CODE)" $?
py "$TMP/brd-api.json" "isinstance(d['documents'], list)" > /dev/null
check "…with a document list" $?

# Too-short speech is refused before a provider is ever called: it costs quota and
# produces a fabricated document from nothing.
CODE=$(curl -s -o "$TMP/brd-short.json" -w '%{http_code}' -X POST -H "$AUTH_HEADER" -H 'content-type: application/json' -d '{"spokenText":"um"}' "$API_BASE/api/brd")
case "$CODE" in 400|422) SHORT=0 ;; *) SHORT=1 ;; esac
[ "$SHORT" = "0" ]
check "a two-word utterance is refused rather than written up (got $CODE)" $?

CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/brd/does-not-exist")
[ "$CODE" = "404" ]
check "an unknown document is 404, not a 500 (got $CODE)" $?

CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/brd/does-not-exist/export?format=md")
[ "$CODE" = "404" ]
check "…and so is its export (got $CODE)" $?

# All three of export's error responses are deliberately plain text, not the JSON
# envelope — the caller is a browser navigating a download link, not a fetch reading
# `.error.code` (matching the transcript/BRD export routes elsewhere in this migration).
EXPORT_BODY=$(curl -s -H "$AUTH_HEADER" "$API_BASE/api/brd/does-not-exist/export?format=md")
[ "$EXPORT_BODY" = "Not found" ]
check "…as plain text, not a JSON error envelope" $?
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$API_BASE/api/brd/does-not-exist/export?format=md")
[ "$CODE" = "401" ]
check "an unauthenticated export is refused too (got $CODE)" $?

CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH_HEADER" "$API_BASE/api/brd/does-not-exist/export?format=pdf")
[ "$CODE" = "400" ]
check "an unsupported export format is refused before the lookup (got $CODE)" $?

head_ "nav routing: Recordings vs Transcript reader (SPEC-012 §2.1)"
#
# These two modules share the `/dashboard/transcripts` prefix and used to share the exact
# same href, so both lit up as current on every transcripts URL. `active()` counts how many
# sidebar links carry aria-current on a page, and `current()` names which one does — the
# assertion is "exactly one, and it is the right one", because a nav that highlights two
# destinations is as wrong as one that highlights none.
navlinks() {
  python3 - "$1" <<'PYEOF'
import re, sys
html = open(sys.argv[1], encoding='utf-8', errors='replace').read()
for m in re.finditer(r'<a\b[^>]*>', html):
    tag = m.group(0)
    href = re.search(r'href="([^"]*)"', tag)
    if href and 'aria-current' in tag:
        print(href.group(1))
PYEOF
}

curl -s -o "$TMP/nav-transcripts.json" -H "$AUTH_HEADER" "$API_BASE/api/transcripts"
READER_TARGET=$(py "$TMP/nav-transcripts.json" "[t['id'] for t in d['transcripts'] if t['counts']['segments'] > 0][0]")
if [ -z "$READER_TARGET" ]; then
  dim "  (no transcript with segments; skipping the nav-routing block)"
else
  curl -s -o "$TMP/nav-list.html" "$BASE/dashboard/transcripts"
  LIST_ACTIVE=$(navlinks "$TMP/nav-list.html" | grep -c '^/dashboard/transcripts' || true)
  [ "$LIST_ACTIVE" = "1" ]
  check "on the library, exactly one transcripts nav entry is current (got $LIST_ACTIVE)" $?

  LIST_WHICH=$(navlinks "$TMP/nav-list.html" | grep '^/dashboard/transcripts' | head -1)
  [ "$LIST_WHICH" = "/dashboard/transcripts" ]
  check "…and it is Recordings (got ${LIST_WHICH:-none})" $?

  curl -s -o "$TMP/nav-reader.html" "$BASE/dashboard/transcripts/$READER_TARGET"
  READ_ACTIVE=$(navlinks "$TMP/nav-reader.html" | grep -c '^/dashboard/transcripts' || true)
  [ "$READ_ACTIVE" = "1" ]
  check "on a transcript, exactly one transcripts nav entry is current (got $READ_ACTIVE)" $?

  READ_WHICH=$(navlinks "$TMP/nav-reader.html" | grep '^/dashboard/transcripts' | head -1)
  [ "$READ_WHICH" = "/dashboard/transcripts/reader" ]
  check "…and it is Transcript reader (got ${READ_WHICH:-none})" $?

  # The landing route exists so the nav entry has somewhere to go.
  CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/dashboard/transcripts/reader")
  REDIRECT=$(curl -s -o /dev/null -w '%{redirect_url}' "$BASE/dashboard/transcripts/reader")
  case "$CODE" in 200|307|308) LANDING=0 ;; *) LANDING=1 ;; esac
  [ "$LANDING" = "0" ]
  check "/dashboard/transcripts/reader resolves (got $CODE)" $?

  printf '%s' "$REDIRECT" | grep -q '/dashboard/transcripts/[a-z0-9]'
  check "…redirecting to a readable transcript (${REDIRECT:-no redirect})" $?
fi

# The marketing link was removed from the sidebar as poor UX (ROADMAP X.19). It must not
# come back by accident — it sat one row above Sign out, which is exactly the misclick
# the original comment claimed to be avoiding.
curl -s -o "$TMP/nav-dash.html" "$BASE/dashboard"
grep -qiF 'Visit the website' "$TMP/nav-dash.html"
NOLINK=$?
[ "$NOLINK" != "0" ]
check "the sidebar offers no 'Visit the website' link (ROADMAP X.19)" $?

head_ "cleaning up after itself"
#
# The suite creates throwaway accounts rather than mutating the development identity —
# which is correct, but it also has to remove them. Seven orphans per run accumulated
# into dozens, and a user list where the real accounts are outnumbered ten to one is
# not a usable one.
#
# The pattern is deliberately narrow: a known fixture prefix, digits, and @acme.test.
# It cannot match an address a person would sign up with.
if command -v docker > /dev/null 2>&1; then
  FIXTURE_PATTERN="^(renametest|pwtest|pwtest2|smoke|cookiecheck|resettest|welcome)-[0-9]+@acme\\.test$"

  BEFORE=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT count(*) FROM \"User\" WHERE email ~ '$FIXTURE_PATTERN';" 2>/dev/null | tr -d ' \r')

  # Everything owned by a fixture account cascades from the User row.
  docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "DELETE FROM \"User\" WHERE email ~ '$FIXTURE_PATTERN';" > /dev/null 2>&1

  AFTER=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT count(*) FROM \"User\" WHERE email ~ '$FIXTURE_PATTERN';" 2>/dev/null | tr -d ' \r')

  [ "${AFTER:-1}" = "0" ]
  check "removed ${BEFORE:-?} fixture account(s), leaving none behind" $?

  # And the accounts it must never have touched are still there.
  REMAINING=$(docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "SELECT count(*) FROM \"User\" WHERE email !~ '$FIXTURE_PATTERN';" 2>/dev/null | tr -d ' \r')
  [ "${REMAINING:-0}" -ge 1 ]
  check "${REMAINING:-?} real account(s) untouched" $?

  # Transcripts uploaded by the ingest section are removed by that section itself; this
  # catches any left by an early failure so a re-run starts clean.
  docker compose exec -T db psql -U vowcraft -d vowcraft -tAc \
    "DELETE FROM \"Transcript\" WHERE title IN ('Smoke meeting', 'Screen recording', 'No audio');" > /dev/null 2>&1
fi

printf '\n─────────────────────────────────────────────────────────\n'
if [ "$FAIL" = "0" ]; then
  green "$PASS passed, 0 failed"
  exit 0
else
  red "$PASS passed, $FAIL FAILED"
  exit 1
fi
