/**
 * `ProfileView` — SPEC-005 §2, §3, §6, §8.
 *
 * The use cases that used to live here (get/rename/change password/onboarding/deletion)
 * are now served by FastAPI (`apps/api/app/services/settings_profile.py`'s
 * `ProfileService`, plus `change_password` on the Auth surface's `AuthService`; SPEC-015
 * §7). This interface is kept as a type-only export because the frontend's `import type`
 * still describes the FastAPI response with it, and duplicating the shape elsewhere would
 * just be a second place for it to drift.
 *
 * `purgeExpiredDeletions` (a hard-delete sweep for accounts past their grace period) did
 * not come with it: nothing in this codebase — no route, no `scripts/`, no
 * `docker-compose.yml` entry — ever called it, on either side, before or after this
 * cutover. It is a pre-existing gap this migration did not introduce and does not fix; a
 * real deletion sweeper is a separate feature, not a porting task.
 */

export interface ProfileView {
  id: string
  email: string
  name: string | null
  image: string | null
  createdAt: string
  /** Whether a password is set, never the hash itself (SPEC-005 §9.3). */
  hasPassword: boolean
  passwordUpdatedAt: string | null
  onboardingCompletedAt: string | null
  onboardingSkipped: boolean
  deletionRequestedAt: string | null
  deletionEffectiveAt: string | null
  minPasswordLength: number
}
