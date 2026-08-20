import { handle, jsonBody } from '@/lib/http'
import { setSessionCookie } from '@/lib/session-cookie'
import { login, loginSchema } from '@/server/auth/service'

export const dynamic = 'force-dynamic'

/** POST /api/auth/login — verifies the password and issues a session. */
export function POST(req: Request) {
  return handle(req, async (ctx) => {
    const input = loginSchema.parse(await jsonBody(req))
    const result = await login(input, ctx.requestId)
    await setSessionCookie(result.session)
    return { ok: true, user: result.user, redirectTo: '/dashboard' }
  })
}
