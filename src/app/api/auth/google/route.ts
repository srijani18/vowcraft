import { NextResponse } from 'next/server'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'
import { startGoogleSignIn } from '@/server/auth/google'

export const dynamic = 'force-dynamic'

/**
 * GET /api/auth/google — redirects to Google's consent screen.
 *
 * A redirect rather than JSON so it can be a plain link. Failures redirect back to
 * the login page with a readable reason instead of dead-ending someone mid-flow on
 * an error boundary.
 */
export async function GET(req: Request) {
  const login = new URL('/login', env().APP_URL)
  try {
    const next = new URL(req.url).searchParams.get('next') ?? undefined
    return NextResponse.redirect(await startGoogleSignIn(next ?? undefined))
  } catch (err) {
    logger.warn('auth.google_start_failed', { err })
    login.searchParams.set('error', (err as Error).message)
    return NextResponse.redirect(login)
  }
}
