import { handle, jsonBody } from '@/lib/http'
import { setSessionCookie } from '@/lib/session-cookie'
import { signup, signupSchema } from '@/server/auth/service'

export const dynamic = 'force-dynamic'

/** POST /api/auth/signup — creates the account and signs it in. */
export function POST(req: Request) {
  return handle(req, async (ctx) => {
    const input = signupSchema.parse(await jsonBody(req))
    const result = await signup(input, ctx.requestId)
    await setSessionCookie(result.session)
    return { ok: true, user: result.user, redirectTo: '/dashboard' }
  })
}
