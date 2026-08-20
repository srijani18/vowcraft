# SPEC-000 — System Architecture

**Status:** Accepted · **Owner:** Architecture · **Supersedes:** none

## 1. Purpose

Voice2BRD turns spoken conversations into *executed* outcomes. The pipeline is:

```
capture → transcribe → diarize → extract → classify risk → approve (human) → execute → audit
```

This spec fixes the module boundaries so each phase can be built and deployed
independently. SPEC-001..003 detail the slice implemented in this repository:
**the Action Items Dashboard and its execution path.**

## 2. Deployment topology

| Component | Runtime | Hosting | Notes |
|---|---|---|---|
| `web` | Next.js 15 (App Router, RSC) | Vercel / Docker | UI + API routes. Stateless. |
| `db` | PostgreSQL 16 | Managed / Docker | System of record. |
| `asr` | Python (FastAPI, WhisperX + pyannote) | Render / Docker | **Out of scope for this slice**; contract in §5. |

Rule: the Node tier never loads an ML model. Heavy inference lives in `asr` and
communicates over an HTTP contract, so the hybrid local/cloud split in the
roadmap is a deployment choice, not a rewrite.

## 3. Layering (dependency direction is one-way, downward)

```
app/          Next.js routes + React components   — no business logic
  ↓
server/       use-cases / orchestration           — transactions, audit, dispatch
  ↓
domain/       pure business logic                 — no I/O, no imports from lib/db
  ↓
integrations/ outbound provider adapters          — Google, Notion, Gmail, Slack
  ↓
lib/          cross-cutting primitives            — db, env, logger, retry, crypto
```

Enforced conventions:

- `domain/**` is **pure**: no `fetch`, no Prisma, no `process.env`. It is unit
  testable without a database. Guardrails live here.
- `server/**` is the only layer allowed to write to the database *and* the only
  layer allowed to emit audit records.
- `integrations/**` adapters are interchangeable behind `IntegrationProvider`
  (SPEC-002 §3) and must be side-effect free at import time.
- `app/api/**` handlers do three things only: parse+validate input, call a
  service, map the result to HTTP. Any `if` about business meaning is a bug.

## 4. Trust model

The browser is untrusted. Consequences that shape the code:

1. Guardrails and risk classification are **re-evaluated server-side at execute
   time**, never read from the request body (SPEC-003 §6).
2. The client may propose an execution payload; the server recomputes the
   effective payload and echoes back what it actually used.
3. `status` transitions are validated against the state machine in SPEC-001 §5,
   not assigned from client input.

## 5. Upstream contract (extraction → dashboard)

The extractor (Phase 2, separate spec) writes `ActionItem` rows. This slice
requires exactly these fields to be populated, and nothing more:

```jsonc
{
  "description": "Send the revised budget to Priya",  // verb-first imperative
  "actionType": "EMAIL",            // CALENDAR | TASK | EMAIL | REMINDER | NONE
  "ownerName": "Marcus",
  "ownerEmail": "marcus@acme.test", // nullable
  "deadline": "2026-08-22T17:00:00Z",// nullable
  "priority": "HIGH",               // HIGH | MEDIUM | LOW
  "confidence": "MEDIUM",           // HIGH | MEDIUM | LOW
  "sourceTimestampMs": 754000,      // offset into the recording
  "sourceQuote": "…I'll get the revised budget over to Priya by Friday…",
  "reasoning": "Speaker 2 accepted the task and named a deadline.",
  "payload": { "to": ["priya@acme.test"], "subject": "Revised budget" }
}
```

`payload` is provider-shaped and **may be incomplete** — completing it is the
dashboard's job (SPEC-001 §6). Anything the extractor cannot ground in the
transcript it must leave `null` rather than invent.

## 6. Observability

Structured JSON logs on one line per event, correlated by `requestId`
(generated per request, returned in `x-request-id`). Every execution attempt
emits `execution.attempt` / `execution.result` with `actionItemId`, `provider`,
`durationMs`, `outcome`. The `AuditLog` table is the durable, queryable twin of
these logs and is append-only (SPEC-003 §7).

## 7. Non-goals for this slice

Transcription, diarization, streaming ASR, semantic search, and the analytics
dashboard. Their tables exist in the schema so the dashboard can join to real
transcript context, but no ASR code ships here. See `specs/ROADMAP.md`.
