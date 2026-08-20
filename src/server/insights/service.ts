/**
 * `DecisionView` / `DecisionsResult` — SPEC-020.
 *
 * The reading surface for `Decision` rows lives entirely in FastAPI
 * (`apps/api/app/services/decisions.py`). These are kept as type-only exports because the
 * frontend's `import type` still describes the FastAPI response with them, the same
 * convention `src/server/analytics/service.ts` and `src/server/ingest/service.ts` use.
 */

export interface DecisionView {
  id: string
  statement: string
  decidedBy: string | null
  sourceTimestampMs: number | null
  sourceTimestampLabel: string | null
  sourceQuote: string | null
  createdAt: string
  transcript: { id: string; title: string; recordedAt: string | null }
}

export interface DecisionsResult {
  decisions: DecisionView[]
  total: number
  facets: { transcripts: { id: string; title: string }[]; decidedBy: string[] }
  nextCursor: string | null
}
