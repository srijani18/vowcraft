import { handle, jsonBody } from '@/lib/http'
import { forgotPasswordSchema, requestReset } from '@/server/auth/reset'

export const dynamic = 'force-dynamic'

/**
 * POST /api/auth/forgot-password — SPEC-007 §2.3.
 *
 * Returns an identical status and body whether the address is registered, is a
 * Google-only account, or has never existed. Anything else makes this an account
 * enumerator, which is the same reason sign-in has one generic failure.
 */
export function POST(req: Request) {
  return handle(req, async (ctx) => {
    const input = forgotPasswordSchema.parse(await jsonBody(req))
    const ip = req.headers.get('x-forwarded-for')?.split(',')[0]?.trim() ?? null
    const result = await requestReset(input, { ip, requestId: ctx.requestId })

    return {
      ok: true,
      // Deliberately uninformative about the account.
      message:
        'If that address has an account, a reset link is on its way. It expires in an hour.',
      delivered: result.delivered,
      // Only ever set outside production, and only when no mail provider is
      // configured — it is what makes the flow usable on a fresh self-hosted instance.
      devLink: result.devLink,
    }
  })
}
