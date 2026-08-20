import { NextResponse } from 'next/server'
import { randomUUID } from 'node:crypto'
import { env } from '@/lib/env'
import { getProvider } from '@/integrations/registry'
import { completeAuthorization } from '@/integrations/oauth'
import { logger } from '@/lib/logger'

export const dynamic = 'force-dynamic'

interface RouteParams {
  params: Promise<{ provider: string }>
}

/** GET /api/integrations/:provider/callback — verifies state, exchanges the code. */
export async function GET(req: Request, { params }: RouteParams) {
  const { provider: providerId } = await params
  const url = new URL(req.url)
  const settingsUrl = new URL('/dashboard/settings/integrations', env().APP_URL)
  const requestId = `req_${randomUUID().replace(/-/g, '').slice(0, 16)}`

  const denied = url.searchParams.get('error')
  if (denied) {
    settingsUrl.searchParams.set('error', `Authorization was declined (${denied}).`)
    return NextResponse.redirect(settingsUrl)
  }

  const code = url.searchParams.get('code')
  const state = url.searchParams.get('state')
  if (!code || !state) {
    settingsUrl.searchParams.set('error', 'The provider did not return an authorization code.')
    return NextResponse.redirect(settingsUrl)
  }

  try {
    const provider = getProvider(providerId)
    if (!provider) {
      settingsUrl.searchParams.set('error', `Unknown integration “${providerId}”.`)
      return NextResponse.redirect(settingsUrl)
    }

    await completeAuthorization(provider, code, state, requestId)
    settingsUrl.searchParams.set('connected', provider.displayName)
    return NextResponse.redirect(settingsUrl)
  } catch (err) {
    logger.warn('oauth.callback_failed', { provider: providerId, requestId, err })
    settingsUrl.searchParams.set('error', (err as Error).message)
    return NextResponse.redirect(settingsUrl)
  }
}
