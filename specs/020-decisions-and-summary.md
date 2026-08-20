# SPEC-020 — Decisions & summary insights

**Status:** Accepted · **Implements:** Phase 3.12 · **Fulfils:** the reading half of SPEC-010 §7's extraction contract
**Depends on:** SPEC-010 (extraction), SPEC-003 §7 (the audit log's pagination pattern, reused here)

## 1. Purpose

SPEC-010 §7 already extracts a `summary` and a `decisions[]` array on every transcript,
and has since the extraction pipeline first shipped. Until now, both were write-only:
`POL_CONTRADICTS_DECISION` (`apps/api/app/domain/rules/policy.py`) reads decisions to
flag a contradicting action item, and the transcript reader shows a bare count — but
nothing let a person actually read what a meeting decided, or search across meetings for
it. This spec is the reading surface for data that has existed, fully populated, since
SPEC-010. No extraction change, no new column, no migration.

## 2. Scope

**In:** a cross-transcript, paginated, filterable feed of every decision the caller's
meetings have produced (`GET /api/decisions`), a dashboard page at
`/dashboard/insights` built on it, a "recent meeting summaries" panel reusing
`Transcript.summary` (already returned by `GET /api/transcripts`), and a per-transcript
"N decisions →" link from the transcript reader, matching the existing action-items link.

**Out, deliberately:**
- **Risks and open questions.** Neither is extracted — `EXTRACTION_TOOL`
  (`apps/api/app/domain/extraction.py`) has no such field. Adding them means changing
  what the LLM is asked to produce, which is SPEC-010's concern, not this one's.
- **Contradiction warnings as a feature of this page.** `POL_CONTRADICTS_DECISION`
  already surfaces a contradiction as a guardrail message on the Action Items dashboard,
  where the item it blocks lives. Reproducing it here would be a second, competing home
  for the same warning.
- **A write or correction path.** A `Decision` row has no `status` column, and
  `services/extraction.py` deletes and fully replaces a transcript's decisions on every
  re-extraction — there is nothing here for a human to approve, edit, or reject.

## 3. Why a cross-transcript feed, not a per-transcript list

Both existing reading surfaces this module's `SPEC-020` slug sits alongside in the nav
— Action Items and the Audit Log — are cross-transcript aggregates with keyset
pagination and facets, not per-meeting widgets. Decisions fit the same shape for the
same reason: they have no in-progress lifecycle to work through one meeting at a time,
so the useful question is almost always retrospective ("what did we decide, and
when") — a question that gets more useful the more meetings it can search across, not
less. The per-transcript touchpoint stays a count-with-a-link into this feed, the exact
convention `TranscriptReader.tsx` already uses for action items.

## 4. Contract

`GET /api/decisions` — read-only, `CurrentUser`-scoped through the caller's own
transcripts.

| param | type | notes |
|---|---|---|
| `transcriptId` | string | exact match, one meeting |
| `decidedBy` | string, max 200 | case-insensitive |
| `q` | string, max 200 | substring match against `statement` OR `sourceQuote` |
| `limit` | int, 1–200, default 50 | |
| `cursor` | string | opaque `Decision.id` from a previous page's `nextCursor` |

Response:
```json
{
  "decisions": [
    {
      "id": "dec_123",
      "statement": "We will ship the invoicing module in October",
      "decidedBy": "Priya Raman",
      "sourceTimestampMs": 154300,
      "sourceTimestampLabel": "2:34",
      "sourceQuote": "so let's commit to shipping invoicing in October",
      "createdAt": "2026-08-19T10:00:00+00:00",
      "transcript": { "id": "tr_456", "title": "Sprint planning", "recordedAt": "2026-08-19T09:00:00+00:00" }
    }
  ],
  "total": 42,
  "facets": {
    "transcripts": [{ "id": "tr_456", "title": "Sprint planning" }],
    "decidedBy": ["Priya Raman", "Alex Kim"]
  },
  "nextCursor": "dec_789"
}
```

Pagination is keyset on `(createdAt, id)` — the same technique `AuditService` uses and
for the same reason: `Decision` has no priority-like column to order by, `id` breaks
ties within one extraction call's timestamp (every decision from a meeting is written
in the same transaction), and a seek stays stable under concurrent inserts where
`OFFSET` would not.

`sourceTimestampMs` is typed `Optional[int]` defensively, matching `EXTRACTION_TOOL`'s
schema, but is not reachable as `null` from today's extraction path:
`validate_extraction` defaults a missing value to `0` before persisting. Worth stating
here so it is not "fixed" later as a bug that was never live.

## 5. Frontend

`src/app/dashboard/insights/page.tsx` — server component, forwards `searchParams` to
`/api/decisions` verbatim (same pattern as `audit-log/page.tsx`) and separately fetches
`/api/transcripts` for the summaries panel, reusing the already-returned `summary` field
rather than adding a second one.

`src/components/insights/InsightsView.tsx` — client component: free-text search,
a transcript picker and decided-by chips built from `facets`, a "recent meeting
summaries" rail, and a decision list where each row links back to
`/dashboard/transcripts/{id}?t={sourceTimestampMs}` — the same citation convention
action items already use.

`src/lib/navigation.ts`'s `insights` module moves from `planned` to `live`; its
`highlights` were trimmed to match what actually ships (§2's "out" list).

## 6. Testing

`apps/api/tests/test_decisions_service.py` — scoping, filters (`transcriptId`,
`decidedBy`, `q` against both `statement` and `sourceQuote`, and a regression pin that
`q` does not match an unrelated internal id), facets computed from the unfiltered
scope, and the same three-test pagination suite `test_audit_service.py` uses
(more-than-a-page yields a cursor, a cursor continues with no gap or overlap, rows
sharing one timestamp still paginate cleanly).

`_timestamp_label` was promoted out of `action_items.py` into
`app/domain/action_item.py` as a public `timestamp_label`, since decisions cite a
transcript moment the same way action items do — `test_domain.py::TestTimestampLabel`
covers it directly as a pure function.
