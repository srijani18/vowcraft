/**
 * `SearchHit` / `SearchResult` — SPEC-021.
 *
 * The reading surface lives entirely in FastAPI (`apps/api/app/services/search.py`,
 * `apps/api/app/api/routes/search.py`). Kept as type-only exports because the frontend's
 * `import type` still describes the FastAPI response with them, the same convention
 * `src/server/insights/service.ts` and `src/server/analytics/service.ts` use.
 */

export interface SearchHit {
  segmentId: string
  transcriptId: string
  transcriptTitle: string
  speakerLabel: string | null
  text: string
  contextBefore: string | null
  contextAfter: string | null
  startMs: number
  timestampLabel: string | null
  score: number
}

export interface SearchResult {
  results: SearchHit[]
}

export interface ReindexResult {
  status: string
  transcripts: number
}
