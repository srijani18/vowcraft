import { NextResponse } from 'next/server'
import { randomUUID } from 'node:crypto'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'
import { encodeSession } from '@/lib/session'
import { cookieOptions } from '@/lib/session-cookie'
import { SESSION_COOKIE_NAME } from '@/lib/session'
import { completeGoogleSignIn } from '@/server/auth/google'

export const dynamic = 'force-dynamic'

/**
 * GET /api/auth/google/callback — SPEC-007 §3.2.
 *
 * Sets the cookie on the redirect response directly rather than through
 * `cookies().set()`: this handler returns a 302, and attaching the Set-Cookie to
 * that exact response is the unambiguous way to have it survive the redirect.
 */
export async function GET(req: Request) {
  const url = new URL(req.url)
  const login = new URL('/login', env().APP_URL)
  const requestId = `req_${randomUUID().replace(/-/g, '').slice(0, 16)}`

  const denied = url.searchParams.get('error')
  if (denied) {
    login.searchParams.set('error', denied === 'access_denied'
      ? 'Google sign-in was cancelled.'
      : `Google sign-in failed (${denied}).`)
    return NextResponse.redirect(login)
  }

  const code = url.searchParams.get('code')
  const state = url.searchParams.get('state')
  if (!code || !state) {
    login.searchParams.set('error', 'Google did not return an authorization code.')
    return NextResponse.redirect(login)
  }

  try {
    const result = await completeGoogleSignIn(code, state, requestId)

    const destination = new URL(
      result.nextPath && result.nextPath.startsWith('/') ? result.nextPath : '/dashboard',
      env().APP_URL,
    )
    if (result.outcome === 'created') destination.searchParams.set('welcome', '1')

    const response = NextResponse.redirect(destination)
    response.cookies.set(SESSION_COOKIE_NAME, encodeSession(result.session), cookieOptions())
    return response
  } catch (err) {
    logger.warn('auth.google_callback_failed', { requestId, err })
    login.searchParams.set('error', (err as Error).message)
    return NextResponse.redirect(login)
  }
}
