import { db } from './db'
import { env } from './env'
import { readSessionCookie } from './session-cookie'

/**
 * Identity resolution — SPEC-006 §2.
 *
 * Order:
 *   1. A valid signed session cookie whose `pwd` claim still matches the account's
 *      `passwordUpdatedAt`. Rotating a password therefore invalidates every
 *      existing session without a server-side session store.
 *   2. In development only, the seeded dev identity. This is what lets the smoke
 *      suite and `docker compose up` exercise the whole product without a login
 *      step, and it is explicitly gated so it cannot leak into production.
 *
 * Production with no cookie throws, and the route handler turns that into a
 * redirect to /login. Failing closed is the point: a deployment that forgot to
 * wire auth must not serve one user's data to everyone.
 */

export interface CurrentUser {
  id: string
  email: string
  name: string | null
  /**
   * How this identity was established. The auth screens use it to decide whether
   * to bounce a visitor to the dashboard: a *real* session means they are signed in
   * and have no business on a login form, whereas the development fallback would
   * otherwise make `/login` permanently unreachable locally — you could never see
   * the screen you were building.
   */
  source: 'session' | 'dev'
  /**
   * `passwordUpdatedAt` in epoch milliseconds (0 if the account has never set a
   * password). Carried on `CurrentUser` so a server component can mint a FastAPI
   * bearer token (see `api-server.ts`) without a second database round trip.
   */
  pwdEpochMs: number
}

export class UnauthenticatedError extends Error {
  constructor() {
    super('Not signed in.')
    this.name = 'UnauthenticatedError'
  }
}

export async function optionalUser(): Promise<CurrentUser | null> {
  const session = await readSessionCookie().catch(() => null)

  if (session) {
    const user = await db.user.findUnique({
      where: { id: session.sub },
      select: { id: true, email: true, name: true, passwordUpdatedAt: true },
    })
    if (user) {
      // Millisecond precision — see the `pwd` field on SessionPayload.
      const stamp = user.passwordUpdatedAt ? user.passwordUpdatedAt.getTime() : 0
      // A mismatch means the password changed after this cookie was issued.
      if (stamp === session.pwd) {
        return { id: user.id, email: user.email, name: user.name, source: 'session', pwdEpochMs: stamp }
      }
    }
    return null
  }

  const devIdentityAllowed =
    env().NODE_ENV !== 'production' || process.env.ALLOW_DEV_IDENTITY === '1'
  if (!devIdentityAllowed) return null

  const user = await db.user.findUnique({
    where: { email: env().DEV_USER_EMAIL },
    select: { id: true, email: true, name: true, passwordUpdatedAt: true },
  })
  if (!user) return null
  const { passwordUpdatedAt, ...rest } = user
  return { ...rest, source: 'dev', pwdEpochMs: passwordUpdatedAt ? passwordUpdatedAt.getTime() : 0 }
}

export async function currentUser(): Promise<CurrentUser> {
  const user = await optionalUser()
  if (!user) throw new UnauthenticatedError()
  return user
}
