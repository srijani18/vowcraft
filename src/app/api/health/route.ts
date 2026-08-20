import { db } from '@/lib/db'
import { env } from '@/lib/env'
import { encryptionAvailable } from '@/lib/crypto'
import { providerStatuses } from '@/integrations/registry'
import { handle } from '@/lib/http'
import { deliveryConfigured } from '@/server/email/mailer'
import { googleConfigured } from '@/server/auth/google'
import { ffmpegAvailable } from '@/lib/ffmpeg'

export const dynamic = 'force-dynamic'

/** Liveness plus the configuration facts an operator needs at a glance. */
export function GET(req: Request) {
  return handle(req, async () => {
    const started = Date.now()
    let database: 'up' | 'down' = 'down'
    try {
      await db.$queryRaw`SELECT 1`
      database = 'up'
    } catch {
      database = 'down'
    }

    return {
      status: database === 'up' ? 'ok' : 'degraded',
      database,
      latencyMs: Date.now() - started,
      integrationsMode: env().INTEGRATIONS_MODE,
      encryptionConfigured: encryptionAvailable(),
      // Surfaced so an operator learns from a health check that production cannot
      // send a reset email, rather than from a confused user (SPEC-007 §2.5).
      passwordResetDeliverable: deliveryConfigured(),
      googleSignInConfigured: googleConfigured(),
      // Without FFmpeg, audio uploads still work but video cannot be demuxed — worth
      // knowing from a health check rather than from a failed upload.
      videoUploadSupported: await ffmpegAvailable(),
      providers: providerStatuses(),
    }
  })
}
