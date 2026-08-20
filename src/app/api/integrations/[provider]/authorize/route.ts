import { NextResponse } from 'next/server'
import { currentUser } from '@/lib/auth'
import { env } from '@/lib/env'
import { getProvider } from '@/integrations/registry'
import { startAuthorization } from '@/integrations/oauth'
import { logger } from '@/lib/logger'

export const dynamic = 'force-dynamic'

interface RouteParams {
  params: Promise<{ provider: string }>
}

/**
 * GET /api/integrations/:provider/authorize — SPEC-002 §4.
 *
 * Redirects rather than returning JSON, so it can be a plain link in the UI.
 * Failures redirect back to the settings page with a readable reason instead of
 * dead-ending the user on an error page mid-OAuth.
 */
export async function GET(req: Request, { params }: RouteParams) {
  const { provider: providerId } = await params
  const settingsUrl = new URL('/dashboard/settings/integrations', env().APP_URL)

  try {
    const provider = getProvider(providerId)
    if (!provider) {
      settingsUrl.searchParams.set('error', `Unknown integration “${providerId}”.`)
      return NextResponse.redirect(settingsUrl)
    }

    const user = await currentUser()
    const { url } = await startAuthorization(provider, user.id)
    return NextResponse.redirect(url)
  } catch (err) {
    logger.warn('oauth.authorize_failed', { provider: providerId, err })
    settingsUrl.searchParams.set('error', (err as Error).message)
    return NextResponse.redirect(settingsUrl)
  }
}
