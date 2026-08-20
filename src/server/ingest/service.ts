/**
 * `TranscriptSummary` / `TranscriptDetail` — SPEC-010 §9, SPEC-012.
 *
 * The use cases that used to live here (upload, list, get, delete, re-extract, pipeline
 * status) are now served by FastAPI (`apps/api/app/services/transcripts.py` and
 * `apps/api/app/services/extraction.py`, SPEC-015 §7). These two interfaces are kept as
 * type-only exports because the frontend's `import type` still describes the FastAPI
 * response with them, and duplicating the shape elsewhere would just be a second place
 * for it to drift.
 */

export interface TranscriptSummary {
  id: string
  title: string
  status: string
  stage: string
  progress: number
  language: string | null
  durationMs: number | null
  diarized: boolean
  recordedAt: string | null
  createdAt: string
  transcribeProvider: string | null
  extractProvider: string | null
  transcribeError: string | null
  extractError: string | null
  extractErrorCode: string | null
  extractModel: string | null
  summary: string | null
  counts: { segments: number; actionItems: number; decisions: number }
}

export interface TranscriptDetail extends TranscriptSummary {
  segments: {
    id: string
    startMs: number
    endMs: number
    text: string
    /** The name to show: the override if set, otherwise the machine's label. */
    speakerLabel: string | null
    /** Needed so the reader can rename the speaker this segment belongs to. */
    speakerId: string | null
    words: { text: string; startMs: number; endMs: number }[]
  }[]
  /** Every speaker on this transcript, for the rename control. */
  speakers: { id: string; label: string; displayName: string | null }[]
}
