/**
 * `Brd` and its nested shapes — SPEC-014 §5.
 *
 * The tool schema, prompts, and generation/revision logic that used to live here are now
 * served by FastAPI (`apps/api/app/domain/brd.py` + `apps/api/app/services/brd.py`,
 * SPEC-015 §7). These interfaces are kept as type-only exports because `VoiceRecorder.tsx`
 * and `BrdView.tsx` still describe the FastAPI response with them, and duplicating the
 * shape elsewhere would just be a second place for it to drift.
 */

export const PRIORITIES = ['MUST', 'SHOULD', 'COULD'] as const

export interface FunctionalRequirement {
  id: string
  requirement: string
  priority: (typeof PRIORITIES)[number]
  /** Why this is needed, when the speaker said why. Null when they did not. */
  rationale: string | null
}

export interface NonFunctionalRequirement {
  id: string
  category: string
  requirement: string
}

export interface Stakeholder {
  /** A role, never an invented person's name — SPEC-014 §5. */
  role: string
  interest: string
}

export interface Risk {
  risk: string
  /** Only when a mitigation was actually stated or is self-evident. */
  mitigation: string | null
}

export interface Brd {
  title: string
  executiveSummary: string
  objectives: string[]
  scope: {
    inScope: string[]
    outOfScope: string[]
  }
  stakeholders: Stakeholder[]
  functionalRequirements: FunctionalRequirement[]
  nonFunctionalRequirements: NonFunctionalRequirement[]
  assumptions: string[]
  risks: Risk[]
  /**
   * The honesty mechanism — SPEC-014 §5.1. Where the model puts what it does not know,
   * instead of inventing a plausible-looking requirement.
   */
  openQuestions: string[]
}
