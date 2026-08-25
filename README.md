# Vowcraft

**Spoken conversation → transcript → extracted action items → human approval → real
side effect in Google Calendar / Notion / Gmail / Slack → immutable audit trail.**

This repository implements the **whole loop**: upload a recording, get it
transcribed with word-level timings, have the conversation read for what it actually
committed to, review the result behind sixteen guardrails, and execute what you
approve — on the record.

- **Architecture, with diagrams:** [ARCHITECTURE.md](./ARCHITECTURE.md)
- **Specifications:** [specs/](./specs) — the code traces to these, section by section
- **Feature → spec → status:** [specs/ROADMAP.md](./specs/ROADMAP.md)
- **Marketing site:** [`../vowcraft-marketing`](../vowcraft-marketing) — separate
  repo and deploy, sharing this palette's token definitions verbatim

---

## Run it

Requires only Docker. **No API keys.**

```bash
git clone <this repo> && cd vowcraft
docker compose up --build
# → http://localhost:3000/dashboard/action-items
```

That gives you a Postgres instance, a migrated schema, a seeded demo meeting, and
a working dashboard where **every action type executes end to end** in mock mode.
`docker compose --profile tools up` adds Adminer on `:8080` to browse the data.

You land on the login screen. Sign up with any email and a passphrase of 12+
characters, or skip it — in development a seeded identity resolves automatically, so
`/dashboard` works without signing in at all.

Then drop a recording on **`/dashboard/transcripts`**. With no API key at all, a
bundled sample runs the entire pipeline — transcript, word timings, action items,
decisions, guardrails — so the product demonstrates itself before you configure
anything. Add a free Groq or Gemini key and it reads your own recordings.

Verify it behaves as specified — two layers, each doing what the other cannot:

```bash
npm test                 # 329 unit tests over the pure logic — no DB, no network, ~2s
sh scripts/smoke.sh      # 198 end-to-end checks against a running stack
```

The suite really does approve and execute an item, so a second run against the same
database warns you and skips the consumed fixtures. `docker compose down -v` resets.

### Without Docker

```bash
cp .env.example .env      # set DATABASE_URL; APP_ENCRYPTION_KEY=$(openssl rand -base64 32)
npm install
npx prisma db push && npm run db:seed
npm run dev
```

---

## Your data, and how not to lose it

Accounts live in the `vowcraft_db-data` Docker volume, which **survives**
`docker compose down`, `restart`, and `up --build`. It is destroyed only by
`docker compose down -v` or `docker volume rm` — nothing in normal use needs either.

To reset the demo fixtures without touching accounts:

```bash
npm run demo:reset      # re-seeds the demo meetings only
```

**Never run `docker compose down -v` directly.** It deletes the volume and every account
in it, with no warning. If you genuinely need a fresh database, use:

```bash
npm run dev:reset       # backs accounts up, asks, then wipes and restarts
```

The smoke suite also snapshots any real accounts to `backups/` before it starts, so a
later wipe is recoverable. Backups live on the host, not in the volume.

The smoke suite is safe to run against an instance with real accounts: it creates its
own throwaways (`*-<pid>@acme.test`), scopes every mutation to them, removes them
afterwards, and asserts that real accounts were untouched. It used to run an unscoped
`UPDATE "User" SET "passwordHash" = NULL` — which silently destroyed every password in
the database. That is fixed and pinned by a test.

Because `-v` is one character away from `down`, backups exist:

```bash
npm run db:backup:accounts   # identity, settings, roster, keys — the irreplaceable part
npm run db:backup            # everything
npm run db:restore backups/vowcraft-accounts-<stamp>.sql
```

An accounts restore is idempotent, replaces the seeded account cleanly, and re-seeds
the demo fixtures afterwards. Dumps contain password hashes and encrypted credentials,
so `backups/` is gitignored.

## The screens

| Route | What it is |
|---|---|
| `/login` · `/signup` | Credential auth plus Google sign-in: scrypt hashing, a strength meter, show/hide, per-field errors |
| `/forgot-password` · `/reset-password` | Single-use hashed reset tokens that revoke every session on use |
| `/dashboard/transcripts` | Upload a recording; watch it transcribe and extract |
| `/dashboard` | Overview — pending decisions, the workflow funnel, guardrail activity, usage |
| `/dashboard/action-items` | The review board (below) |
| `/dashboard/audit-log` | The append-only trail, filterable, with raw before/after JSON |
| `/dashboard/settings` | Preferences — the guardrail envelope, each field naming the rule it governs |
| `/dashboard/settings/profile` | Name, password rotation, the product tour, account deletion |
| `/dashboard/settings/credentials` | The BYOK vault — 31 providers, free tiers first |
| `/dashboard/settings/integrations` | OAuth connections and their state |
| `/dashboard/modules/<slug>` | Modules that are specified but not yet built, described honestly |

Navigation is a collapsible module rail that becomes a hamburger drawer under
`lg`, driven by one registry in [`src/lib/navigation.ts`](./src/lib/navigation.ts)
that also feeds the dashboard's feature explorer. A first-run walkthrough with
Back / Next / **Skip** persists server-side, so a dismissed tour never reappears.

## What you can do on the dashboard

The seed produces a meeting whose items exercise every path:

| Group | Count | Why |
|---|---|---|
| **Ready to Execute** | 4 | complete, owned, passing every guardrail — one per action type |
| **Needs Clarification** | 2 | one missing `startsAt` + `attendees`, one low-confidence external email |
| **Informational** | 3 | two notes with nothing to execute, one rejected item |

A second meeting adds the edge cases: a **superseded** action ("meeting with Peter"
→ "meeting with Peter and Jordan"), a **dependency chain** that refuses to run
until its blocker is executed, a **Saturday 3-hour meeting** that is approved yet
still blocked by two guardrails, and one **already-executed** item.

Per item: approve, edit (modal built from the same field table the executor
validates against), reject, defer, or execute. Execution always goes through a
confirmation modal showing the exact payload, the provider, the consequence in
words, and any warnings to acknowledge. Keyboard: `⌘K` search, then `a` / `r` /
`d` / `e` on a focused card.

---

## Safety model in one table

| Concern | Mechanism |
|---|---|
| Session cookies | `Secure` is derived from the **scheme of `APP_URL`**, not `NODE_ENV` — the Docker image is a production build served over `http://localhost`, and keying off `NODE_ENV` sent a `Secure` cookie that strict browsers silently dropped |
| Passwords | scrypt (N=2¹⁵) with a per-account salt and a **versioned** cost factor, so it can be raised later without invalidating anyone's password |
| Session revocation | The cookie carries `passwordUpdatedAt` in **milliseconds**; rotating a password invalidates every existing session with no server-side store |
| Account enumeration | Wrong password and unknown address return an identical status *and* message, with a dummy hash evening out the timing |
| Open redirect | `?next=` is honoured only for same-origin paths beginning with a single `/` |
| Nothing executes without a human | Risk tiers gate execution; `autoExecuteLowRisk` defaults to **false** ([SPEC-003 §5](./specs/003-guardrails-approvals.md)) |
| The client cannot lie | Risk and all 16 guardrails are recomputed server-side at execute time; `riskTier`/`violations` in a request body are ignored |
| No accidental real-world writes | `INTEGRATIONS_MODE=mock` is the **default**; mock results carry `simulated: true` and the UI labels them |
| No duplicates | Idempotency key = `sha256(itemId + payload)`, checked before the status gate, so an identical repeat replays |
| No duplicate customer email | Gmail send is treated as non-idempotent: a post-dispatch timeout records `FAILED, uncertain: true` rather than resending |
| Nothing unexplained | `executionResult` records `payloadUsed` on **failure** too; `AuditLog` is append-only |
| A timeout is not consent | Approval requests expire into `DEFERRED`, never into approved |
| Secrets at rest | AES-256-GCM with the ciphertext bound to `userId:service` via AAD; no read path for plaintext |

---

## Bring your own keys

If a provider key is absent from the environment, add it per user at
**`/dashboard/settings/credentials`** — encrypted at rest, masked in every
response, and free tiers listed first with their actual allowances.

**31 providers across 5 modules**, 14 of them free or fully local:

| Module | Free / local | Free credit | Paid |
|---|---|---|---|
| Transcription | Groq Whisper turbo, local WhisperX | AssemblyAI, Deepgram, ElevenLabs | OpenAI |
| Extraction | Gemini, Groq, Cerebras, Ollama | OpenRouter, Mistral, Together, Cohere, HF | Anthropic, OpenAI, DeepSeek, xAI, Azure |
| Translation | DeepL, LibreTranslate | Google Translate | — |
| Semantic search | Voyage, Jina, local BGE | — | OpenAI embeddings |
| Integrations | Notion, Slack, Google OAuth | SendGrid | — |

`EMAIL` has two adapters and you choose per user in Preferences. **Gmail** sends as
you and can save a draft — the reversible option, so it is the default. **SendGrid**
sends as the organisation from a verified domain and needs no per-user consent. It
*cannot* save a draft, and because a draft is classified low risk precisely because
nothing is sent, its validator **refuses** a draft payload rather than delivering
one: routing to SendGrid can never quietly turn a low-risk approval into real
outbound email.

A module with no key reports `unavailable` or stays `mocked` — never a 500 at
request time. See [SPEC-004](./specs/004-credential-vault.md).

---

## API

| Route | Purpose |
|---|---|
| `GET /api/action-items` | list with 9 composable filters, group counts, facets |
| `PATCH /api/action-items/:id` | status transitions + edits; writes a `Correction` and an `AuditLog` row |
| `POST /api/action-items/:id/execute` | dry-run preview or real execution (12-step pipeline) |
| `POST /api/action-items/bulk` | approve / reject / defer up to 100, per-item outcomes |
| `GET /api/credentials` · `PUT`/`DELETE /:service` · `POST /:service/verify` | the BYOK vault |
| `GET /api/integrations/:provider/authorize` · `/callback` | OAuth + PKCE |
| `POST /api/transcripts` | multipart upload; `202` + id, or `200` when identical bytes were already sent |
| `GET`/`DELETE` `/api/transcripts/:id` | poll progress, read the transcript, or remove it |
| `POST /api/transcripts/:id/extract` | re-extract alone, preserving decided items |
| `GET /api/transcripts/:id/audio` | range-request audio streaming for the player |
| `GET /api/pipeline/status` | which stages are usable, and via which provider |
| `POST /api/auth/signup` · `login` · `logout` · `google` | credential and federated auth |
| `POST /api/auth/forgot-password` · `reset-password` | account recovery |
| `GET`/`PATCH` `/api/settings` | the guardrail envelope |
| `GET`/`PATCH`/`DELETE` `/api/profile` · `POST /api/profile/password` | identity, rotation, deletion |
| `GET /api/audit-log` | the append-only trail; no write verb exists |
| `GET /api/overview` | aggregates for the landing dashboard |
| `GET /api/health` | liveness, mode, encryption status, provider registry |

Errors use one envelope with a stable, branchable `code`:

```json
{ "error": { "code": "guardrail_blocked", "message": "…", "details": [ … ] } }
```

---

## Extending it

Both extension points are deliberately the cheapest changes in the codebase,
because they are the two a reviewer will probe:

**A new integration** — implement `IntegrationProvider` (five methods) in
`src/integrations/<name>.ts`, add one line to `registry.ts`. No change to `app/`,
`server/`, or `domain/`.

**A new guardrail** — one file in `src/domain/rules/`, one line in its registry.
`domain/` is pure: no database, no network, no `process.env`, and `now` is passed
in rather than read, so "no meetings on a Sunday" is a test rather than a hope.

**A new module in the nav** — one entry in `src/lib/navigation.ts`. It appears in
the sidebar, the mobile drawer, and the dashboard explorer at once.

---

## Tests

Two layers, because they catch different things.

**`npm test`** — 329 unit tests over the pure layer, in about a second. No database,
no network, no build step: Node 22+ runs the TypeScript directly, and a 30-line
resolve hook (`tests/register.mjs`) handles the `@/*` alias. The suite imports the
*same* files the application imports, so there is no compiled artifact in between
that could diverge.

What it covers that end-to-end tests cannot reach cheaply: every guardrail against a
**fixed clock** (so "no meetings on a Sunday" is genuinely asserted, not hoped for),
risk escalation in every combination, the ciphertext AAD binding and tamper
rejection, session forgery and expiry, backoff jitter bounds, idempotency-key stability under
key reordering, and every palette pairing in both themes.

It also includes `tests/domain-purity.test.ts`, which mechanically enforces
[SPEC-003 §10.5](./specs/003-guardrails-approvals.md): `domain/` may not import
`lib/db`, `integrations/`, `server/`, `process.env`, `fetch`, or `Date.now()`. That
claim is the reason every guardrail is testable at all, so it is checked rather than
trusted — one convenience import would quietly make the rule engine need a database.

**`sh scripts/smoke.sh`** — 198 checks against a running stack, covering the things
only a real system shows: HTTP status codes, transaction boundaries, cookie
round-trips, that an audit row and its state change commit together, and that no
secret reaches a log line.

---

## Look and feel

Palette `#224248 · #325E6A · #44A1A4 · #FF9A00`, in light and dark, with a
three-state theme toggle (system → light → dark) applied before first paint. **A
first-time visitor gets their operating system's preference**; the toggle then wins
in both directions.

The four colours are used verbatim: the two darks are the dark theme's panel and
card surfaces *and* the light theme's two text tiers (`#224248` at 9.7:1, `#325E6A`
at 6.4:1). The teal and the orange are the fills in both themes.

Two measurements shaped everything else. `#44A1A4` is **2.8:1** on a pale ground and
`#FF9A00` is **1.9:1** — so neither accent can carry body text. Accent *text* uses a
tint of the same hue (`#57B6B9` dark, `#327779` light), and both fills carry the same
`#0E2126` label in either theme, so a button is pixel-identical across modes. Since
those fills also fall under WCAG 1.4.11's 3:1 bar for a component *boundary* on
light, each draws a darker ring rather than relying on the fill — the button has to
be findable; the fill is just paint.

The dark page is `#0E2126`, one step deeper than `#224248` on the same hue. With
`#224248` as the canvas, `#325E6A` cards separate by only 1.5:1 and the teal drops
to 3.5:1; going deeper lets both given darks work as real surfaces.

Teal and orange are split by weight rather than merged: teal is interactive (links,
focus, active nav, secondary actions), orange is the consequential action — Execute,
Save, Create account. A page where everything is the CTA colour has no CTA.

`ok` green and `danger` red are the two hues added outside the palette, because it
contains neither and orange is already doing CTAs *and* warnings — three meanings on
one hue is a real regression in a risk-communication tool. Colour never carries
meaning alone: every badge pairs it with an icon and a label.

All of it is measured by `tests/contrast.test.ts`, which parses the values out of
`globals.css` so it cannot pass while the stylesheet disagrees. Details in
[SPEC-006 §5](./specs/006-auth-and-design.md).

## Project layout

```
specs/                    the specifications the code traces to
ARCHITECTURE.md           complete flow, with mermaid diagrams
prisma/schema.prisma      17 models · seed.mjs builds the demo meeting
src/app/                  routes and React components — no business logic
src/server/               use cases: transactions, dispatch, audit
src/domain/               pure business logic: readiness, risk, 16 rules
src/integrations/         provider adapters + OAuth token store
src/lib/                  db, env, logger, retry, crypto, credential vault
scripts/smoke.sh          executable acceptance criteria
docker-compose.yml        db + web + optional adminer
```

## The pipeline

```
upload → transcribe → extract → review → execute → audit
```

**Upload** ([SPEC-010 §4](./specs/010-ingest-transcribe-extract.md)) takes audio or
video up to 25 MB, typed by its **magic bytes rather than its extension** — a
renamed PDF is refused. Video is accepted as a container and the provider strips the
audio track, so **no FFmpeg ships**: the Node image stays slim and the deploy stays
trivial. Identical bytes are content-addressed, so re-uploading the same recording
reuses the transcript instead of spending a second slice of a free tier.

**Transcription** runs *after* the response — an hour of audio outlasts any sensible
request timeout — with progress on the row so a reload never loses it. Word-level
timings are persisted to the table the player expects. Groq's Whisper turbo is the
default because its free tier covers roughly eight hours a day on a key that needs no
card. Speaker labels are **honestly absent**: Whisper does not diarize, so segments
are unattributed and the UI says so rather than implying one speaker.

**Extraction** is a single tool-use call whose schema *is* the frozen contract, so the
model fills a declared shape rather than being asked for JSON and trusted. Six rules
are in the prompt, each because its absence produces a specific failure — and one is
verified rather than trusted: **every `sourceQuote` is checked against the transcript**,
and a paraphrase downgrades confidence to LOW instead of passing as evidence.

Payloads are **derived in code, never requested**. Asking a model for `attendees[]`
invites invented attendees; asking for `durationMinutes` invites a confident 30 nobody
said. A meeting with no stated time gets no `startsAt` at all — which is exactly what
the "needs clarification" lane exists to surface.

The two stages fail independently. A transcript whose extraction rate-limited keeps
the transcript and can be re-extracted alone, and re-extraction **preserves every item
a human has already decided on**.

## Not in this slice

A bot that *joins* a call as a participant. Live meetings are captured by sharing a
browser tab instead (SPEC-013 §1 sets out why: Google's Meet Media API gates bot access
behind a developer-preview programme requiring every participant enrolled). Self-hosted
transcription and embeddings — the `asr` container the catalogue's local entries describe —
are also out; the capabilities they would provide, diarization included, are reached through
a provider instead.

Known gaps stated rather than implied: no per-device session revocation, the audit log
is append-only by code path and database grant rather than hash chaining, and the
bundled sample extractor answers only for the sample recording — it refuses real audio
rather than inventing plausible action items for something it cannot read.
