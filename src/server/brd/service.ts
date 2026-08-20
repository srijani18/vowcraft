/**
 * `BrdSummary` / `BrdDetail` — SPEC-014 §4, §6, §8.
 *
 * The use cases that used to live here (create/refine/rename/delete/list/get) are now
 * served by FastAPI (`apps/api/app/services/brd.py`'s `BrdService`, SPEC-015 §7). These
 * interfaces are kept as type-only exports because the frontend's `import type` still
 * describes the FastAPI response with them, and duplicating the shape elsewhere would
 * just be a second place for it to drift.
 */

import type { Brd } from './schema'

export interface BrdSummary {
  id: string
  title: string
  status: string
  provider: string | null
  model: string | null
  lastError: string | null
  revisionCount: number
  requirementCount: number
  openQuestionCount: number
  createdAt: string
  updatedAt: string
}

export interface BrdRevisionView {
  id: string
  ordinal: number
  spokenText: string
  changeSummary: string | null
  createdAt: string
}

export interface BrdDetail extends BrdSummary {
  content: Brd | null
  revisions: BrdRevisionView[]
}
