/**
 * `TeamMemberView` — SPEC-005 §4.2.
 *
 * Type-only, following the pattern the other post-migration surfaces use: the roster is
 * served entirely by FastAPI (`apps/api/app/services/team.py`), and this interface exists so
 * the frontend's `import type` describes that response in one place rather than restating
 * the shape wherever it is rendered.
 *
 * `email` is the payload here, not `name`. The name is only the lookup key extraction
 * matches a spoken word against; the address is what turns a recognised name into an action
 * that can actually execute (`_resolve_email` in `apps/api/app/domain/extraction.py`).
 */

export interface TeamMemberView {
  id: string
  name: string
  email: string
  /** Free text, shown for context. Null rather than an empty string when unset. */
  role: string | null
}
