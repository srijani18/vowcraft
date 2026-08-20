import { record } from '@/lib/audit'
import { optionalUser } from '@/lib/auth'
import { db } from '@/lib/db'
import { handle } from '@/lib/http'
import { clearSessionCookie } from '@/lib/session-cookie'

export const dynamic = 'force-dynamic'

/** POST /api/auth/logout — clears the cookie. Idempotent by design. */
export function POST(req: Request) {
  return handle(req, async (ctx) => {
    const user = await optionalUser()
    await clearSessionCookie()
    if (user) {
      await record(db, { event: 'auth.signed_out', actorId: user.id, requestId: ctx.requestId })
    }
    return { ok: true, redirectTo: '/login' }
  })
}
