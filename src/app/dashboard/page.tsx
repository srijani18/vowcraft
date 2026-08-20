import { VoiceRecorder } from '@/components/brd/VoiceRecorder'
import { currentUser } from '@/lib/auth'

export const dynamic = 'force-dynamic'

/**
 * `/dashboard` — the voice-to-BRD surface (SPEC-014 §2).
 *
 * The landing route, because this is the tool's primary act. It renders a single record
 * button and nothing that competes with it; the analytics overview it replaced lives at
 * `/dashboard/overview`.
 *
 * Transcription status is resolved by the component against the API backend rather than
 * here: it depends on a bearer token held in the browser, which a server component cannot
 * see. This page therefore only guards the route.
 */
export default async function DashboardPage() {
  await currentUser()

  return (
    <div className="mx-auto max-w-3xl">
      <VoiceRecorder />
    </div>
  )
}
