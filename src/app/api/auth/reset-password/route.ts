import { handle, jsonBody } from '@/lib/http'
import { setSessionCookie } from '@/lib/session-cookie'
import { completeReset, inspectToken, resetPasswordSchema } from '@/server/auth/reset'

export const dynamic = 'force-dynamic'

/**
 * GET  — validates a token without consuming it, so the page can show a useful state
 *        on load rather than failing after the user has typed a new password.
 * POST — sets the password, burns the token, revokes every existing session
 *        (SPEC-007 §2.4), and signs the user in.
 */
export function GET(req: Request) {
  return handle(req, async () => {
    const token = new URL(req.url).searchParams.get('token') ?? ''
    if (!token) return { valid: false, reason: 'unknown' as const }
    return inspectToken(token)
  })
}

export function POST(req: Request) {
  return handle(req, async (ctx) => {
    const input = resetPasswordSchema.parse(await jsonBody(req))
    const result = await completeReset(input, ctx.requestId)
    await setSessionCookie(result.session)
    return { ok: true, user: result.user, redirectTo: '/dashboard' }
  })
}
