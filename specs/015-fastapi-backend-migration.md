# SPEC-015 — FastAPI backend migration

*Moving the API surface from Next.js route handlers to a standalone FastAPI service, without
a rewrite: the database schema, the guardrail behaviour, the security envelopes and the audit
trail all have to survive unchanged.*

## 1. Purpose

The original architecture was one Next.js application: pages, API routes and business logic
in a single deployable. The target architecture is three: a Next.js frontend on Vercel, a
FastAPI backend on Render, and managed Postgres — also on Render, but administered
separately, because a database is not a process you deploy.

Three things make this a migration rather than a rewrite:

- **The schema is authoritative and untouched.** `packages/db`'s SQLAlchemy models describe
  the *existing* Prisma-created tables. §3 states how that was verified.
- **Security-critical behaviour ports byte-for-byte.** Password hashes and encrypted BYOK
  secrets written by the Node implementation must decrypt under the Python one, or every
  existing account and every stored key breaks on cutover.
- **Guardrail and risk logic ports behaviourally.** The 16 rules, the risk classifier and the
  approval gate must produce the *same verdicts*, not merely similar ones — a guardrail that
  disagrees with its own prior behaviour is a guardrail nobody can trust.

## 2. Repository layout

```
vowcraft/
├── src/                  Next.js frontend (route handlers here are being retired per-surface)
├── apps/api/             FastAPI backend
│   ├── app/
│   │   ├── main.py       app factory, CORS, the structured-request-logging middleware
│   │   ├── api/          routes/, dependencies.py (auth), errors.py (envelope)
│   │   ├── core/         config, security (JWT), crypto, passwords, logging, exceptions
│   │   ├── domain/       pure — guardrails, risk, payload schema, BRD contract, timeutil
│   │   ├── integrations/ provider registry + the five adapters (mock/live branch each)
│   │   ├── services/     application services: one per vertical (auth, brd, executor, …)
│   │   ├── schemas/      Pydantic request/response models
│   │   └── prompts/      LLM prompt text, kept out of services/ so it can be read in isolation
│   ├── tests/            pytest — the migration's acceptance gate, see §6
│   └── Dockerfile
├── packages/db/          SQLAlchemy models + Alembic migrations. Not a deployable service —
│                         imported by apps/api, and its migrations run as a release step.
└── docker-compose.yml    three services locally: db, api, web
```

`apps/web` (the target name for the frontend package) does not exist yet. Moving `src/` there
is deferred until the API surface is fully migrated — see §7.

## 3. Database compatibility

`packages/db`'s models were authored against, and verified against, the live Prisma-created
schema — not designed independently and hoped to match. The check that matters:

```
alembic check   →   No new upgrade operations detected.
```

That sentence means the SQLAlchemy models and the live schema agree on every column, type,
nullability, default, index, uniqueness declaration, foreign key, and its `ON DELETE`/
`ON UPDATE` behaviour, and on every enum member. It was reached by generating a baseline
migration against an *empty* database and diffing its result against the live one — 370
schema facts compared, byte-identical — not by assuming the models were right.

Four real divergences were caught this way and are recorded because each is a category of
mistake worth watching for on any future model change:

| Divergence | Consequence if shipped |
|---|---|
| `idempotencyKey` declared globally unique | Rejects attempt 2 of every retried execution — the retry policy (SPEC-002 §6) would have been unusable. |
| `updatedAt` given a `server_default` | Prisma's `@updatedAt` has **no** database default; every INSERT 500'd on a not-null violation. |
| Five `UserSettings` defaults typed from memory | Wrong on all five (timezone, workday end, buffer minutes, budget limit, currency) — three feed the guardrail engine directly, so a FastAPI-created account would have enforced different rules than a Node-created one. |
| `AuditLog`/`ActionItem` cascade rules assumed rather than read | Prisma declares `CASCADE` in two places I had guessed `SET NULL` for. |

**Rule for future schema work:** a column default is part of the contract, not an
implementation detail — compare it explicitly, and prefer `server_default` sourced from what
the live column actually has over a Python-side literal, so the value lives in one place.

### 3.1 A self-healing migration state, because two services share one database

`web`'s entrypoint runs `prisma db push --accept-data-loss` on every boot to keep the schema
in sync with `schema.prisma` — and Prisma has never heard of `alembic_version`, since it
isn't part of that schema. Whichever service restarts second finds the tracking table gone
and every real table Prisma manages still present and correct. `alembic upgrade head` alone
cannot tell that apart from a genuinely fresh database, and tries to recreate every table
from scratch — "already exists" on the first `CREATE TABLE`.

`packages/db/ensure_migration_state.py` runs immediately before `alembic upgrade head` in
`docker/api-entrypoint.sh` and tells the two cases apart the same way debugging this by hand
does: if `alembic_version` is missing but the schema's own tables already exist, the database
is not unmigrated, it is unstamped — so it stamps the **baseline** and lets `upgrade head`
replay every migration after it. Verified by deliberately reproducing the trigger
(`docker compose restart web` between two `api` restarts) and confirming `api` starts clean
rather than crash-looping.

**It stamps the baseline, not head.** The first version stamped `head`, which marks every
migration as applied without running any of them — so a database Prisma had recreated
silently missed everything added after the baseline. That shipped and bit: the migration
altering `AuditLog`'s foreign key (so an audit trail survives deleting an action item) was
stamped over and never ran, leaving the schema disagreeing with the models and no error
anywhere to say so. The baseline is the right revision to stamp because `schema.prisma` *is*
the baseline's snapshot — it is precisely the state a `db push` reproduces.

This makes one invariant load-bearing: **every migration after the baseline must be
idempotent**, since this path re-runs it against a database Prisma has already shaped. Use
`IF NOT EXISTS` / `IF EXISTS`, or guard on a reflected check — and guard *per object*, not
per migration. `add_segment_embeddings` is the cautionary case: `schema.prisma` carries a
shadow `SegmentEmbedding` model (SPEC-021 §4), so a push can create the table while being
unable to express its HNSW index at all. A single "table exists, skip everything" guard
would leave semantic search on a sequential scan — correct results, quietly terrible
latency. A migration that fails on a second run blocks every container start.

Verified by reverting the foreign key by hand, dropping `alembic_version`, and restarting
`api`: all three post-baseline migrations replayed and the schema was repaired.

## 4. Security compatibility

`app/core/passwords.py` and `app/core/crypto.py` reproduce `src/lib/password.ts` and
`src/lib/crypto.ts` exactly:

- scrypt, N=2¹⁵/r=8/p=1, 16-byte salt, 64-byte key, NFKC-normalised input, format
  `s1.<salt>.<hash>`.
- AES-256-GCM, 12-byte IV, format `v1.<iv>.<tag>.<ciphertext>`, AAD binding
  `cred:<userId>:<service>`.

Both were verified in both directions against the *live* TypeScript implementation — not
against a second Python implementation of the same spec, which would only prove Python agrees
with itself. Python decrypts/verifies values Node produced; Node decrypts/verifies values
Python produced. 24 checks, Unicode and empty-string payloads included.

`hashlib.scrypt` is not used: it is unavailable when Python links against LibreSSL (macOS
system Python, among others), so the KDF comes from `cryptography`, which is a dependency
either way.

### 4.1 Auth model: JWT, not a cookie

The Node session was a signed, httpOnly cookie — viable because the page and the API shared
an origin. A separate frontend origin (Vercel) and backend origin (Render) changes this:
`SameSite=Lax` (or stricter) is not sent cross-site at all, so the browser needs to carry a
credential explicitly.

The backend issues a **bearer token pair** — a 30-minute access token and a 14-day refresh
token — instead. One property of the old design is deliberately kept: the `pwd` claim holds
`passwordUpdatedAt` in **milliseconds**, and a token whose claim disagrees with the user's
current value is rejected. That is what makes "change your password, every other session is
signed out" work with no server-side session table. Millisecond precision matters because the
columns are `timestamp(3)` and Postgres *rounds* rather than truncates on write — a value
computed in Python's native microsecond precision would disagree with what the database
actually stored about half the time, producing an intermittent, unreproducible logout
immediately after sign-in. `vowcraft_db.clock.now_ms()` exists for exactly this reason.

The browser holds the token in `sessionStorage`, attaches it as `Authorization: Bearer …` via
`src/lib/api-client.ts`, and refreshes transparently on a 401.

**A React Server Component cannot do the above** — it runs during SSR with the session
cookie (`next/headers`), not the browser's `sessionStorage`. Rather than converting every
server-rendered dashboard page into a client component (losing SSR and adding a loading
flash), `src/lib/api-token.ts` mints its own 60-second access token for whichever user the
session cookie resolved to, and `src/lib/api-server.ts` (`apiServerFetch`/`apiServerJson`)
calls the backend with it, server to server, over `INTERNAL_API_URL` — the Compose-internal
`http://api:8000`, distinct from `NEXT_PUBLIC_API_URL`, which is what the *browser* uses and
which `web`'s own "localhost" cannot reach `api` through from inside its container. This is
only safe because `web` and `api` share `JWT_SECRET` — the token minted here is a real token
`decode_token` accepts, not a proxy or an impersonation channel. Verified byte-for-byte
compatible in both directions: `apps/api/tests/test_web_minted_tokens.py` decodes a real
token captured from `api-token.ts`, and `tests/api-token.test.ts` covers the signing side.

## 5. Layering

```
HTTP route → dependency (auth, settings, session) → Pydantic schema
           → application service → domain logic (pure) → repository → database
```

Enforced by convention rather than a lint rule, the same way `domain-purity.test.ts` polices
it on the TypeScript side: a route file stays a few lines — bind, authenticate, delegate,
translate the domain error to an HTTP one — because the rules and the state changes live one
layer down, in `services/`, where they can be tested without a request object.

## 6. What "equivalent test coverage" means here

Before any frontend surface is repointed from Next.js to FastAPI, that surface has:

1. **Behavioural parity tests** — the Python implementation run against the same fixtures as
   the TypeScript one, asserting identical output (not merely "no errors"). This is how the
   17 guardrail rules (25/25 identical verdicts), the password policy (45/45), and the
   idempotency hashing (8/8 byte-identical) were checked.
2. **A pytest suite** committed under `apps/api/tests/`, covering the ported behaviour on its
   own terms — currently 299 tests.
3. **An integration pass against the live database and, for anything touching a provider, the
   live container** — not a mock database, so a lazy-loading or transaction-boundary mistake
   surfaces before it reaches a user. Several mistakes were caught exactly this way and only
   this way: a missing SQLAlchemy relationship, an async session refresh that re-expired an
   already-loaded relationship, and (for action items) a `list_items` response missing
   pagination and facets entirely, a `bulk` endpoint whose request/response shape didn't match
   what the frontend sends, and a `computeReadiness` port that silently dropped the
   low-confidence and no-owner checks — none of which a unit test against a mock session would
   have caught, because nothing was exercising the actual wire contract or the actual schema.
   `apps/api/tests/test_action_items_service.py` runs against a dedicated `vowcraft_test`
   database (never the one real accounts live in — see `db_session` in `conftest.py`), created
   once via `alembic upgrade head` and given a fresh rolled-back transaction per test.

## 7. Cutover, per surface, not all at once

| Surface | Backend | Frontend calls | Next.js route |
|---|---|---|---|
| Speech (streaming ASR relay) | FastAPI | FastAPI directly | **deleted** |
| Action items, execution | FastAPI, tested per §6 | FastAPI directly (list/patch/bulk/execute via `apiFetch`; first paint via `apiServerJson`) | **deleted** |
| Transcripts, ingest (upload, demux, transcription, extraction) | FastAPI, tested per §6 | FastAPI directly (upload/poll/export/speaker-rename via `apiFetch`; audio/export via an authenticated blob fetch, not a plain `src`/`href`; first paint via `apiServerJson`) | **deleted** |
| Settings | FastAPI, tested per §6 | FastAPI directly (`apiFetch`/`apiServerJson`) | **deleted** |
| Profile (get/rename/onboarding/deletion) | FastAPI, tested per §6 | FastAPI directly (`apiFetch`/`apiServerJson`); password change specifically goes to the Auth surface's `POST /api/auth/change-password`, not a profile-prefixed route | **deleted** |
| Audit log | FastAPI, tested per §6 | FastAPI directly (`apiFetch`/`apiServerJson`) | **deleted** |
| Credentials (BYOK vault) | FastAPI, tested per §6 | FastAPI directly (`apiFetch`/`apiServerJson`) | **deleted** |
| Auth (signup/login/refresh/me/logout/password) | FastAPI | Next.js (session cookie) *and* FastAPI (token, alongside) | still present, by design — see §4.1 |
| BRD | FastAPI, tested per §6 | FastAPI directly (`apiFetch`/`apiServerJson`; export via an authenticated blob fetch, the same pattern as transcripts' audio/export) | **deleted** |

Speech, action items (execution included — its endpoint lives under
`/api/action-items/{id}/execute`, the same surface), transcripts/ingest, settings,
profile, the audit log, credentials, and BRD are fully cut over: the frontend calls FastAPI
exclusively, and the Next.js implementation was deleted — `src/app/api/action-items/` and
`src/server/execution/` for the first, `src/app/api/transcripts/`,
`src/app/api/pipeline/status/`, `src/server/ingest/*.ts`, `src/server/transcription/*.ts`,
and the ingest-only parts of `src/server/extraction/*.ts` for the second,
`src/app/api/settings/route.ts` for the third, `src/app/api/profile/`
(get/rename/password/onboarding, three route files) for the fourth,
`src/app/api/audit-log/route.ts` for the fifth, `src/app/api/credentials/` (four route
files: list, save/delete, verify, status) for the sixth, and `src/app/api/brd/` (four
route files: list/create, get/rename/delete, refine, export) plus `src/server/brd/generate.ts`,
`prompt.ts`, and `export.ts` for the seventh — rather than left dormant, because dormant code
that looks current is a worse trap than an obvious gap. The audit
log's `listAuditLog`/`parseAuditQuery`/`auditQuerySchema` were trimmed out of
`src/server/analytics/service.ts` the same way; `getOverview` and its types stayed, since
`/api/overview` (not part of this cutover) still calls it from the same file. Credentials
is the one surface whose supporting TS files needed **no changes at all**:
`src/lib/credentials/{service,catalog,types}.ts` stayed completely intact, because
`getOverview` (via `moduleAvailability`) still calls them too, and `catalog.ts`'s
presentation constants (`MODULE_META`, `TIER_META`, …) are pure, DB-free data that
`CredentialsManager.tsx` was already importing directly as a client component — only the
Next.js *route* files that wrapped them were redundant once the frontend's `fetch` calls
were repointed. `src/server/extraction/providers.ts` and `schema.ts` were kept alive through
every earlier surface specifically because BRD generation depended on them
(`resolveExtractor`/`ExtractionError`, called from the now-deleted `generate.ts`) — once
BRD's own cutover deleted that caller, a grep for `@/server/extraction/` turned up nothing,
so the entire `src/server/extraction/` directory was deleted too, exactly the trigger this
spec anticipated when the two files were first kept back. Their loss uncovered one gap
of its own: `extraction/schema.ts`'s validation (`extractionSchema`) had a Vitest suite
(`tests/extraction-schema.test.ts`) with no Python equivalent anywhere — `validate_extraction`
in `apps/api/app/domain/extraction.py` existed and was already used by the ingest pipeline,
but had never had a single direct test written against it. Ported as
`apps/api/tests/test_extraction_contract.py` (9 tests) before deleting the Vitest file,
matching `validate_extraction`'s actual, documented behaviour — a deliberately lenient,
filter-bad-items-rather-than-reject-the-whole-payload design, unlike BRD's own validator
below — not the original zod schema's stricter all-or-nothing one.
`tests/extraction-errors.test.ts` (the sibling file, covering `ExtractionError`
classification) was deleted without porting, since `apps/api/tests/test_llm_errors.py`
already covered the same logic completely (13 tests).

Action items is the one surface where the repoint uncovered real gaps between the two
backends rather than a clean drop-in — closing them is what most of this cutover actually
was. None were visible from reading the FastAPI code in isolation; every one surfaced only by
comparing behavior against the frontend's actual expectations or against the original
TypeScript's actual behavior, then confirmed live:

- `list_items` had no pagination, no facets, and only two of eight filters. `patch_item`
  supported three of the nine client-editable fields and could write up to three audit rows
  for one PATCH (SPEC-003 §10 needs exactly one). `bulk` used a different request and
  response shape than the frontend sends.
- `execute`'s response was missing every field the confirmation modal and success toast
  actually read — `preview.consequence`/`preview.fields` (nested under `preview`, not
  top-level), `riskTier`/`riskFactors`/`approvalGate`/`warnings` (top-level, not nested),
  and on `result`: `version`, `outcome`, `startedAt`/`finishedAt`, `durationMs`,
  `payloadUsed`, `warnings`, `error`. The confirmation modal would have thrown outright on
  `preview.preview.fields.map(...)` the first time a real user opened it.
- `compute_readiness` (the FastAPI port) was missing the low-confidence and no-owner checks
  present in `src/domain/action-item.ts`'s original — a low-confidence or unowned item with
  an otherwise-complete payload was shown READY (executable) instead of
  NEEDS_CLARIFICATION. Already live and affecting real data before this was noticed; not
  something the migration introduced so much as something it finally exercised.
- `Settings.INTEGRATIONS_MODE` defaulted to `"live"`, the opposite of
  `src/lib/env.ts`'s `"mock"` default for the same field (SPEC-002 §2: "mock is the default
  so an unconfigured app cannot email anyone"). `docker-compose.yml`'s explicit override
  masked this in every local run; a deployment that forgot to set the variable would not
  have had that override.
- `EXECUTED`-adjacent PATCH requests were checked against `SERVER_ONLY_STATUSES` before
  `transition_error()`, so every such request reported `server_only_status` even from a
  status (PROPOSED) that could never legally reach EXECUTED at all — a transition-shape
  question misreported as a permissions one. `illegal_transition` and `blocked_by_dependency`
  had also drifted to `invalid_transition`/`dependency_pending` somewhere in the original
  port. `dependsOn` was hardcoded to `None` in the DTO — the dependency gate was enforced
  correctly server-side but never visible in the API response.
- SendGrid's draft-payload refusal lived only in `execute()` (dispatch time); the original
  TypeScript schema refuses it in `validate()` (a Zod `.refine()`), so a dry run never
  triggered it — the confirmation modal would show a draft as previewable when it can never
  actually run.

All of the above were found by pointing `scripts/smoke.sh`'s action-items assertions at
`$API_BASE` (they had been asserting against the *already-deleted* Next.js routes, so
"repoint the frontend" had quietly outpaced "repoint the acceptance suite") and iterating
until 302 checks passed with 0 failures — not by code review alone. The smoke suite now
authenticates as the seeded demo account through FastAPI directly (`prisma/seed.mjs` gives
it a real password for exactly this, since FastAPI has no dev-identity bypass — see §4.1).

Transcripts/ingest had no pytest coverage at all before this cutover — a prior port had been
written but never exercised against a real request. The audit that preceded the repoint found
37 distinct divergences from the original TypeScript; the most severe ones:

- The pipeline was structurally incapable of completing without a real transcription and
  extraction API key — the bundled zero-key sample fallback that makes `docker compose up` a
  working, self-demonstrating product had not been ported at all. This was the single most
  severe finding: without it, the demo path was not a slow or degraded experience, it was no
  experience.
- Upload's checksum used `sha256` over the raw file bytes; the original hashes the
  **base64-encoded text** of those bytes (`sha256(bytes.toString('base64'))`) — an unusual
  but load-bearing choice, since it is what the dedup check compares against rows written by
  the TypeScript era. Verified byte-identical to the original via direct Node/Python
  execution on the same fixture, not by reading the two implementations and assuming they
  agreed.
- `GET .../audio` advertised `accept-ranges: bytes` but always returned the full file
  regardless of a `Range` header — no seeking, despite claiming to support it. `Range`
  parsing was extracted into a pure `resolve_range()` function and unit-tested directly
  (11 cases: absent, `bytes=start-end`, `bytes=start-`, `bytes=-suffix`, out-of-range, and
  the 416 sentinel) rather than left inline in the route.
- `PATCH .../speakers` accepted one speaker per request and returned the whole transcript;
  the frontend sends a batch of up to 50 in one call and reads back `{ok, speakers}`. Fixed
  with two-stage validation in a specific order — does the transcript exist (404) strictly
  before are all the named speakers actually its own (422) — since collapsing those into one
  query answers a missing-transcript request with the same "unknown speaker" code as a real
  ownership violation.
- Upload's response was missing `ok` and `message` entirely, which made the frontend's
  uploader report every successful upload as a failure.
- `render_export`'s Markdown and WebVTT output diverged from `src/server/ingest/export.ts` on
  the metadata line, the summary blockquote, and — for VTT — used `<v Name>` voice spans where
  the original never did (plain `Name: text`, same convention as SRT and TXT). Rewritten to
  match exactly and pinned with a direct port of `tests/export.test.ts`'s fixture and
  assertions (16 tests), since a subtitle player cannot tell a "close enough" WebVTT file from
  a broken one until it fails to parse it.
- `list_transcripts` issued three `COUNT` queries per row instead of three for the whole page
  — a 100-row library page issuing up to 300 queries. Fixed with `GROUP BY ... WHERE id IN
  (...)` batched counts.
- A second upload of an already-processed recording, arriving as an orphaned-asset edge case,
  risked an `UnboundLocalError` crash rather than the intended "reused, not reprocessed"
  response.
- The audio post-transcode safety checks present in the original (`extraction_empty` for a
  zero-byte result, `audio_too_long` for one still over the provider cap after compression)
  were absent, and duration was read from the wrong probe (the re-encoded target instead of
  the original source, which drifts slightly from the real value).

Repointing the frontend surfaced one more thing no amount of backend testing would have: a
browser cannot attach a bearer token to a plain `<audio src>` or a download `<a href>`, and
the FastAPI backend is a different origin from the frontend (Vercel vs. Render), so the
same-origin session cookie the old Next.js routes relied on for these two endpoints does not
apply. `TranscriptReader.tsx` now fetches both through the authenticated client and hands the
player/download an object URL — which means seeking happens against the in-memory blob, so
the backend's own `Range` support (still correct, and still covered above) goes unused by
this particular caller. `api-client.ts`'s `apiFetch` also had a latent bug this surface was
the first to hit: it set `content-type: application/json` on every request with a body,
including a `FormData` upload, which would have silently broken every file upload by
overriding the browser's own multipart boundary header.

`scripts/smoke.sh`'s ingest section needed two fixes beyond the routing change: two checks
grepped `docker compose logs web` for the pipeline's audio-extraction log line, which now
comes from the `api` container instead; and the FastAPI structured logger, unlike the old
one, puts a space after each JSON key's colon (`"container": "mov"`, not `"container":"mov"`)
— a difference only a live log grep, not a pytest assertion, could have caught.

Settings is small — two endpoints — but every one of its hand-written business rules had
been dropped in the port, silently, because they were encoded the wrong way:

- The response was missing `routingOptions` and `timeZones` entirely — the exact data the
  provider-routing picker and time-zone dropdown need to render any options at all. Another
  instance of this migration's recurring failure mode: a port that reproduces the stored
  fields but not the UI-facing computed ones.
- Workday-ordering (`workdayEnd` after `workdayStart`), time zone existence, and org-domain
  format were not validated at all — a request setting an inverted working day, a nonexistent
  IANA zone, or a garbage domain would simply be accepted and written, each silently
  disabling the guardrail it feeds (`SCHED_HOURS`, the zoned-time comparisons, and
  `POL_EXTERNAL_EMAIL`'s internal/external split, respectively).
- Provider-routing existence (does this provider actually serve this capability) was not
  checked either — `{"providerRouting": {"EMAIL": "notion"}}` would have been accepted and
  stored, even though Notion cannot send email.
- `workdayStart`/`workdayEnd`'s format check *was* present, but as a Pydantic
  `Field(pattern=...)` — which meant a violation answered `400 invalid_request` with a
  generic Pydantic error body, not the documented `422 invalid_clock`. This is a **bug
  class**, not a one-off: any check encoded as a Pydantic field constraint or a
  validator raising `ValueError` lands in FastAPI's generic `RequestValidationError`
  handler, which always answers `400`/`invalid_request` — correct for a genuine type
  mismatch (it is exactly what the original's `ZodError` handling also returns for one,
  see `src/lib/http.ts`), but wrong for a named business-rule error. The fix was moving the
  clock check into an explicit service-layer validation that raises `unprocessable(...)`
  directly, the same way every other business rule in this codebase already does it — never
  a Pydantic-level constraint when the original answers a *specific* code. `budgetApprovalLimit`
  was also missing its upper bound (`le=100_000_000`), and `orgCurrency` was stored in
  whatever case was submitted rather than uppercased, both fixed the same pass.
- `approvalThresholds`, conversely, *was* correctly schema-level in the original (a zod
  `.record(enum, enum)`, which fails the whole request the same generic way as a type
  mismatch) — but the FastAPI port had a validator that silently filtered out invalid
  entries instead of rejecting the request. Fixed by declaring the field as
  `dict[Literal[...], Literal[...]]` and letting Pydantic's own rejection do the work,
  which now answers exactly the same `400 invalid_request` shape as the original's `ZodError`
  path — no custom code needed once the constraint was expressed correctly.

Profile turned up the most severe finding of any surface migrated so far — not a missing
field, but a missing *safety check* on an irreversible action, plus a dead end in a flow
the live frontend actively uses:

- `DELETE /api/profile` (schedules account deletion) had no confirmation check at all. The
  original requires the account's own email typed out — "a checkbox is too easy to click
  through for something irreversible," per its own comment — and `ProfileManager.tsx`
  already sends `{"confirm": ...}` expecting it to be checked. The FastAPI port accepted
  any authenticated `DELETE`, with no body, and scheduled deletion regardless. Fixed by
  requiring `confirm` and rejecting a mismatch with `422 confirmation_mismatch`, matching
  the original exactly (case-insensitive comparison included).
- Restarting the onboarding tour — the live "Show the tour again" button in
  `ProfileManager.tsx` — sent `{"action": "restart"}` to an endpoint whose Pydantic model
  only accepted `{"skipped": boolean}`. There was no way to represent "restart" at all, and
  no `reachedStep` tracking for tour analytics either, both present in the original schema
  and actively read/written by the live frontend (`OnboardingTour.tsx` sends `reachedStep`
  on every skip and completion, not only on restart).
- `minPasswordLength` was missing from the profile response entirely — the password
  strength meter and its hint text in `ProfileManager.tsx` both read it directly.
- Changing a password for an account with no password yet (a Google-only sign-in setting
  one for the first time) was a dead end: `POST /api/auth/change-password` required
  `currentPassword` unconditionally, and even when a caller worked around that, the
  handler refused outright with "This account signs in with Google. Set a password in
  Settings → Profile" — the exact action being attempted, with nowhere else to go. Fixed
  by making `currentPassword` optional and skipping the check when the account has no
  password yet, matching `changePassword` in the original exactly.
- A wrong current password answered `401 unauthenticated` rather than `403
  current_password_incorrect`. The distinction is not cosmetic: `apiFetch`'s browser
  client treats any `401` as an expired session and transparently attempts a token
  refresh and retry — so a wrong password would have silently retried the identical
  failing request instead of showing the error, on the one endpoint most likely to be
  tested with a typo.
- Two conflict codes had drifted from the original's exact strings — a second deletion
  request answered `already_requested` instead of `deletion_already_requested`, and
  cancelling with nothing pending answered `not_requested` instead of
  `no_deletion_pending`. Neither breaks anything on its own (both are still `409`s with a
  message), but a frontend or a future caller matching on the code would silently miss.
- A stale docstring on `request_deletion`, present before this cutover, claimed
  `optional_user` refuses a user with a pending deletion — the opposite of what
  `apps/api/app/api/dependencies.py` actually does (deliberately, so the grace period
  remains usable to cancel). Fixed as part of this pass since it was actively misleading
  about a security-relevant behavior; the code itself was already correct.

Repointing `ProfileManager.tsx`'s password form surfaced one more consequence of password
change living on the Auth surface rather than Profile: rotating the hash revokes every
existing token, including the one the request just authenticated with, so the response is
a fresh token pair rather than a `ProfileView`. The client must call `storeTokens(...)`
immediately, then fetch `GET /api/profile` separately to refresh what the form displays —
skipping the token-store step would leave the browser holding a bearer token FastAPI no
longer accepts, silently signing the user out the moment they change their password.

`scripts/smoke.sh`'s session-cookie tests (SPEC-006 §8, all Next.js-native and out of
scope for this cutover) had leaned on `/api/profile` as a convenient "whose session is
this" probe. With that route gone, the checks that ask *which* account a cookie identifies
now render `$BASE/dashboard` and grep its sidebar for the expected email (`Sidebar.tsx`'s
`AccountFooter` renders it as visible text) instead of reading a JSON field; the check for
a cookie invalidated by a password rotation now asserts the `307` redirect to `/login`
that `optional_user`'s null return actually produces, rather than a `401` JSON body that
no longer exists on this path. The mutations themselves (rename, password change,
onboarding, deletion) now authenticate with a bearer token minted for each throwaway
account, alongside its still-Next.js session cookie where the test also needs one.

The audit log was flagged before its own cutover started (§7's original recon) as having a
known, bounded contract break rather than an unknown one — and closing it was most of the
work:

- The FastAPI port paginated with `offset`/`limit` and returned no `nextCursor` at all,
  while `AuditLogViewer.tsx`'s "Load older events" button is built entirely around a
  cursor from the previous page. Fixed with keyset (seek) pagination on `(at, id)` rather
  than reintroducing `OFFSET`: `id` breaks ties between rows sharing a millisecond — a
  single transaction routinely writes more than one audit row — and a seek does not skip
  or repeat a row if the table changes between page fetches the way `OFFSET` can.
- `actorLabel` and `actionItemDescription` — both rendered directly by `AuditLogViewer.tsx`
  (`entry.actorLabel`, `entry.actionItemDescription`) — were absent from the response
  entirely; the port exposed the raw `actorId` instead, which the frontend never reads.
  Fixed by resolving both from two small batched `IN` queries, matching the original's
  join-based approach without joining `User`/`ActionItem` into every row.
- Free-text search matched `event` OR `requestId` — an opaque internal identifier no user
  would search for. The original matches `event` OR the linked action item's
  *description*, which is what the search field's own placeholder ("Search events and
  action descriptions…") promises. Fixed to match the original exactly.
- `since` and `actionItemId` were entirely unimplemented — no route parameter, no
  filtering. Neither is called by the current UI, but `audit-log/page.tsx` forwards every
  query-string parameter it receives without restricting the set, so a URL using either
  (a bookmark, a future feature, direct navigation) would have silently done nothing.
  Ported for contract completeness, not because a live caller was found.

Credentials was the largest gap found in this entire migration, by a wide margin — not one
missing check but most of a surface:

- The catalogue held 9 of the original's 31 services. Every TRANSLATION and EMBEDDING entry
  was absent, and three of the four INTEGRATION entries — the credentials *page* would have
  shown 9 providers where a user expects 31, with entire modules invisible rather than
  merely unconfigured.
- `POST /{service}/verify` did not exist at all — the "Test" button on every credential
  with a verify recipe. Porting it meant porting the recipe mechanism itself: per-service
  auth mode (bearer / header / query), extra headers, a method override, Slack's specific
  "200 with `ok:false`" rejection (a 2xx alone is not enough evidence a key works), a
  10-second timeout, and the same in-memory per-user/service cooldown as the original
  (accepting the same single-process limitation the original has, rather than introducing a
  distributed rate limiter the source design never called for).
- `GET /credentials/status` — the endpoint the availability banner polls — did not exist
  either. `GET /credentials` itself was also missing `modules` and `encryptionConfigured`
  from its response entirely, returning only `{credentials}`; the banner had no data source
  from *any* route.
- Even for the 9 services that existed, most `CredentialView` fields were missing from the
  response — `blurb`, `tier`, `costNote`, `docsUrl`, `models`, `canVerify`, `alsoUsedBy`,
  `enabled`, `lastVerifiedAt` — and `fields` was a bare list of field-key strings rather than
  the full `{key, label, placeholder, secret, required, help}` objects the settings form
  renders inputs from.
- `sharedFrom` returned the sibling's raw service id (e.g. `"groq"`) rather than its display
  name (`"Groq — Whisper large-v3 turbo"`) — `CredentialsManager.tsx` renders it directly as
  `Via {sharedFrom}`, so the id would have leaked straight into the UI as unexplained
  internals.
- `DELETE` 404'd when nothing was stored for that service — the original's `deleteMany`
  succeeds harmlessly on zero rows, and this matters concretely: `groq_llm` typically never
  has its own row at all (it is satisfied entirely by `groq`'s shared key), so "removing" it
  through the original UI is a normal, successful action that this port would have answered
  with an error.
- A real, subtle bug surfaced only by testing the cooldown twice in the same process:
  `time.monotonic()`'s reference point is undefined by the Python standard library and can
  start near zero, so treating `0.0` as the sentinel for "never verified" meant the very
  first verification of a freshly started process could spuriously 429. Fixed by tracking
  "never verified" as the explicit absence of a dict entry rather than assuming any
  particular numeric value is safely in the past.
- `saveCredential`'s per-field validation — reject the whole write if a service's
  *required* field is missing, e.g. Azure OpenAI's `endpoint`/`deployment` alongside its
  key — was entirely absent; a Pydantic `min_length=1` on the whole `secrets` dict does not
  express "these three specific keys must all be present," so an incomplete multi-field
  save would have been silently accepted and failed only much later, at first use.

One thing this migration deliberately did *not* fix: `notion`, `slack`, and the `google`
OAuth entry are catalogue fixtures whose credential-vault "keys" are never actually read
back by the execution adapters, which use a separate OAuth `IntegrationAccount` flow
instead (only `sendgrid` among the INTEGRATION entries is genuinely consulted at runtime).
That is a pre-existing quirk of the original app, not a migration defect — the credentials
*page* has to look and behave identically either way, decorative entries included.

BRD was the last surface, and the gap it turned up was the sharpest instance yet of a
theme this migration kept re-finding in smaller forms at Settings and Credentials: a bound
from the original zod schema ported as *truncation* instead of *rejection*.

- `validate_brd()` accepted an oversized `title`, `executiveSummary`, or any array/string
  field past its documented limit by silently trimming it to fit, rather than rejecting the
  document. This is not a generic input-hygiene bug for this specific schema: BRD's own
  system prompts state, more than once, "do not invent" and "record an unresolved point as
  an `openQuestion` rather than guessing" — the entire design philosophy is that an LLM
  output the app cannot faithfully represent should surface as a visible failure, not be
  quietly reshaped into something that fits. Fixed by rewriting every bound in
  `apps/api/app/domain/brd.py` to match `src/server/brd/schema.ts`'s zod limits exactly —
  title 3–160, executive summary 10–1200, objectives (max 15, each 3–300), scope
  in/out-of-scope (max 25, each 3–300), stakeholders (max 20, role 2–120, interest 3–400),
  functional requirements (max 80, requirement 3–600, rationale ≤500), non-functional
  requirements (max 40, category 2–60, requirement 3–600), assumptions (max 20, each
  3–300), risks (max 20, risk 3–400, mitigation ≤400), open questions (max 25, each
  3–400, the one field whose per-item cap genuinely differs from the 300 used everywhere
  else) — and to *reject* (append an issue, fail the whole document) on any violation,
  matching zod's own `safeParse` semantics, pinned with 13 new tests in
  `test_brd_contract.py`.
- `refine()`'s `changeSummary` had no upper bound at all — the original caps it at 1200
  characters; unbounded, an adversarial or malfunctioning provider response could grow the
  audit trail without limit.
- The export route mixed several small deviations that only surfaced by reading it next to
  `src/app/api/brd/[id]/export/route.ts` line by line: it used `CurrentUser` (a dependency
  that itself 401s) instead of `OptionalUser` with an explicit `if user is None` check, so
  its 401 came back as a JSON error body rather than the original's plain-text response —
  a difference invisible to a route that returns 200 far more often than 401 in testing,
  but real to any script or tool expecting the documented `text/plain`; the `format` query
  parameter was checked with `Query(pattern=...)`, so an invalid value produced the generic
  Pydantic `400 invalid_request` shape instead of the specific error the route means to
  give; and a document with no generated content answered `400` rather than the original's
  `409`, misreporting a state conflict as a malformed request. `SpokenBody.spokenText`'s
  `max_length` was also off by 10,000 (60,000 instead of the original's 50,000). All four
  fixed in the same pass, with two new `scripts/smoke.sh` regression checks for the
  unauthorized/not-found plain-text behaviour specifically, since that is exactly the kind
  of thing a pytest-green route can still get wrong for a caller that isn't a browser.
- `BrdService` itself had zero pytest coverage of any kind before this cutover — every
  other service migrated so far had at least some tests to audit against; BRD's is new,
  19 tests, covering create/refine success and failure paths (a failed refine leaves the
  row holding its last-known-good document, not a half-written one), malformed
  provider-response handling, revision numbering and id-preservation across edits, the
  audit trail, and cross-user scoping.

BRD was the last surface in this migration's scope. Auth is the only Next.js API code that
remains, and it stays on purpose, not by omission: §4.1 already specifies it as
dual-running indefinitely (a Vercel-hosted frontend keeps a same-origin session cookie for
its own server-rendered pages, while FastAPI issues the bearer token everything else in
this table uses), so there is no further surface left to repoint. With that, every
principle this section accumulated per surface is now closed out rather than a standing
warning for "the next one": settings established that a business-rule validation must be
an explicit service-layer check (raising `unprocessable(...)` directly), never a Pydantic
field constraint, which silently answers the wrong status and code for every value it
rejects. Profile established that a missing confirmation/safety check on a destructive
action does not fail a pytest run — nothing asserts a check is *present*, only that the
ones that exist behave correctly — so reading the original line by line, not the test
suite, is what catches an omission. The audit log established that a pagination *style* is
part of the contract, not an implementation detail free to differ: `offset` and `cursor`
can both be "correct" pagination, but only one of them is what the frontend actually calls
with. Credentials gave the largest version of the same lesson: a "ported" catalogue or
config surface needs its *size* checked, not just its shape — 9 of 31 services all
individually well-formed passed every shape-level check there was, while two-thirds of the
actual product was simply missing. BRD closed the set with the sharpest form of a pattern
first seen in Settings and Credentials: a bound ported as silent truncation rather than
outright rejection is not a rounding error, it is undisclosed data loss, and for a surface
whose entire design philosophy is "never invent, surface what you can't represent" that
distinction is close to the whole point of the feature. Every surface in this migration
was verified the same way regardless of how small it looked going in: read both
implementations side by side rather than trusting that a pytest-green port is a
faithful one, then confirm live against `scripts/smoke.sh` — a discipline that found a real,
previously invisible gap on every single surface it was applied to, without exception.

## 8. Acceptance criteria

1. `alembic check` reports no pending operations against the live schema at every commit that
   touches `packages/db`.
2. A password or BYOK secret written before the migration continues to authenticate/decrypt
   after it, with no forced reset.
3. A guardrail rule change is caught by a parity test before it reaches a behavioural
   difference between the two implementations, for as long as both exist.
4. No Next.js route is deleted while any frontend code still calls it.
5. Every new FastAPI route has a corresponding pytest covering its failure paths, not only
   its success path.
