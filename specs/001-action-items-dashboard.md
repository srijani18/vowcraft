# SPEC-001 — Action Items Dashboard

**Status:** Accepted · **Implements:** Phase 3.1–3.2 · **Depends on:** SPEC-000

## 1. Goal

A reviewer opens `/dashboard/action-items` after a meeting and, without reading
the transcript, can decide the fate of every extracted action in under a minute:
approve it, fix it, reject it, or defer it — then execute what is approved.

## 2. Non-goals

Creating action items by hand; editing the transcript; multi-tenant sharing.

## 3. Route

`/dashboard/action-items` — server component fetches the first page directly via
the service layer (no client round-trip on first paint), then hands off to a
client component for filtering and mutations.

## 4. Vocabulary

- **status** — where the item is in the human workflow (§5).
- **readiness** — a *derived* value (never stored) that answers "could this
  execute right now?" It drives the three groups (§7).
- **riskTier** — how much human ceremony execution requires (SPEC-003 §3).
- **confidence** — how much the extractor trusts itself. Never overridden by the
  system; only a human edit can raise it (to `HIGH`, recorded as a correction).

## 5. Status state machine

```
                 ┌──────────── edit ────────────┐
                 ▼                              │
  PROPOSED ──approve──▶ APPROVED ──execute──▶ EXECUTING ──▶ EXECUTED
     │  │                  │                      │
     │  │                  │                      └──▶ FAILED ──retry──▶ EXECUTING
     │  ├──reject──▶ REJECTED                     
     │  └──defer───▶ DEFERRED ──approve──▶ APPROVED
     └──edit──▶ PROPOSED (payload/details change, status unchanged)
```

Legal transitions, and nothing else:

| from | to | trigger |
|---|---|---|
| `PROPOSED` | `APPROVED`, `REJECTED`, `DEFERRED` | user decision |
| `DEFERRED` | `APPROVED`, `REJECTED`, `PROPOSED` | user decision |
| `APPROVED` | `EXECUTING`, `REJECTED`, `PROPOSED` | execute / withdraw |
| `EXECUTING` | `EXECUTED`, `FAILED` | executor (server only) |
| `FAILED` | `EXECUTING`, `REJECTED` | retry / give up |
| `EXECUTED` | — | terminal |

`EXECUTING` and `EXECUTED` are **server-assigned only**. A `PATCH` from the
client that names either is rejected with `422 illegal_transition`. Rejecting an
already-`EXECUTED` item is refused — the side effect exists in the world; use a
compensating action instead.

## 6. Readiness (derived, pure — `domain/action-item.ts`)

An item is evaluated in this order; the first match wins:

1. `INFORMATIONAL` — `actionType === 'NONE'`, or status is `REJECTED`.
   Nothing to execute; shown for the record.
2. `NEEDS_CLARIFICATION` — any of:
   - a **required payload field for its `actionType` is missing** (§6.1);
   - `confidence === 'LOW'`;
   - `ownerName` is null;
   - a **blocking** guardrail violation exists (SPEC-003 §4).
3. `READY` — everything else.

`EXECUTING` / `EXECUTED` / `FAILED` items keep their readiness but are rendered
in a separate "Execution" lane so the three decision groups stay a to-do list.

### 6.1 Required payload fields per action type

| actionType | required | optional |
|---|---|---|
| `CALENDAR` | `title`, `startsAt`, `durationMinutes`, `attendees[≥1]` | `location`, `description`, `timeZone` |
| `TASK` | `title` | `dueAt`, `assignee`, `notes`, `projectId` |
| `EMAIL` | `to[≥1]`, `subject`, `body` | `cc`, `bcc`, `sendMode` |
| `REMINDER` | `message`, `remindAt`, `channel` | `target` |
| `NONE` | — | — |

Missing fields are surfaced as named chips on the card ("needs: startsAt,
attendees"), so "needs clarification" is never a dead end.

## 7. Layout

```
┌─ Filters ─────────────────────────────────────────────────────────────┐
│ status ▾   priority ▾   owner ▾   deadline ▾   type ▾   [search]  ⌘K  │
└───────────────────────────────────────────────────────────────────────┘

  Ready to Execute (4)        [Approve all] [Execute all approved]
  ├─ card ─ card ─ card ─ card
  Needs Clarification (2)
  ├─ card ─ card
  Informational (3)
  └─ card ─ card ─ card
```

Card, top to bottom: priority stripe · description · owner · deadline ·
`⏱ 12:34` source timestamp · confidence badge · risk badge · action type ·
collapsible source quote · `[Approve] [Edit] [Reject] [Defer]` ·
`[Execute]` (only when status is `APPROVED`).

## 8. Filters

Server-side, all combinable, all reflected in the URL query string so a filtered
view is shareable and survives reload:

| param | type | semantics |
|---|---|---|
| `status` | repeatable enum | OR within, AND across params |
| `priority` | repeatable enum | |
| `owner` | string | case-insensitive exact match on `ownerName` |
| `type` | repeatable enum | `actionType` |
| `readiness` | repeatable enum | applied in-memory after fetch (derived) |
| `deadline` | `overdue \| today \| week \| none` | relative to request time, UTC |
| `q` | string | substring over `description` + `sourceQuote` |
| `transcriptId` | cuid | scope to one meeting |
| `cursor`, `limit` | pagination | `limit` default 50, max 200 |

Sort order is fixed and not user-configurable: priority desc → deadline asc
(nulls last) → `sourceTimestampMs` asc. Rationale: a stable order makes "work
top to bottom" correct, and reviewers lose their place when order shifts.

## 9. API contracts

### `GET /api/action-items`
Query params per §8. Returns:
```jsonc
{
  "items": [ /* ActionItemDTO, see §10 */ ],
  "counts": { "READY": 4, "NEEDS_CLARIFICATION": 2, "INFORMATIONAL": 3, "total": 9 },
  "facets": { "owners": ["Marcus", "Priya"], "transcripts": [{ "id": "…", "title": "…" }] },
  "nextCursor": null
}
```

### `PATCH /api/action-items/:id`
Body — every field optional, at least one required:
```jsonc
{
  "status": "APPROVED",
  "description": "…", "ownerName": "…", "ownerEmail": "…",
  "deadline": "2026-08-22T17:00:00Z", "priority": "HIGH",
  "actionType": "CALENDAR", "payload": { /* replaces payload wholesale */ }
}
```
- Validates the transition (§5). Illegal → `422`.
- Any change to a substantive field records a `Correction` row (SPEC-003 §8) so
  the extractor can learn from it, and writes an `AuditLog` entry.
- Returns the full updated DTO with freshly computed readiness and violations.

### `POST /api/action-items/:id/execute`
Body: `{ "payloadOverride": {…}?, "dryRun": boolean?, "idempotencyKey": string? }`
Behaviour in SPEC-002 §5. Returns `200` with the execution result on success,
`409 approval_required` / `422 guardrail_blocked` / `502 provider_error`.

### `POST /api/action-items/bulk`
`{ "ids": string[], "op": "approve" | "reject" | "defer" }` — max 100 ids.
Per-item outcomes returned; partial success is normal and reported as such
(`{ "results": [{ "id", "ok", "error?" }], "okCount", "failedCount" }`).

## 10. DTO shape

The API never leaks Prisma rows. `ActionItemDTO` adds three computed fields the
client must not recompute: `readiness`, `violations[]`, `missingFields[]`. It
also inlines `transcript: { id, title, recordedAt }` and
`supersededBy: { id, description } | null`.

## 11. Interaction requirements

- Optimistic updates on approve/reject/defer, rolled back on error with a toast
  naming what failed. Execution is **never** optimistic.
- Execute always goes through a confirmation modal showing the *exact* payload,
  the provider, the consequence sentence ("creates a calendar event for 3
  people"), and any non-blocking warnings.
- Keyboard: `⌘K` search, `⌘U` upload, `a`/`r`/`d` on a focused card,
  `?` shortcut help. Focus ring visible; every button reachable by tab.
- Empty, loading (skeleton), and error (boundary + retry) states for every lane.

## 12. Acceptance criteria

1. The seeded meeting “Q3 budget sync” yields exactly 4 Ready / 2 Needs
   clarification / 3 Informational, matching what `db:seed` prints.
2. Approving an item with a missing required payload field is possible, but
   executing it is refused with the field named.
3. A `PATCH` setting `status: "EXECUTED"` returns `422`.
4. Editing a description writes exactly one `Correction` and one `AuditLog` row.
5. Filters compose, survive reload via the URL, and `counts` reflect the filter.
6. Reload after executing shows `EXECUTED` with the provider's returned id.
