import { redirect } from 'next/navigation'
import Link from 'next/link'
import { Button, EmptyState, GlassCard } from '@/components/ui/primitives'
import { apiServerJson } from '@/lib/api-server'
import type { TranscriptSummary } from '@/server/ingest/service'

export const dynamic = 'force-dynamic'

/**
 * `/dashboard/transcripts/reader` — the Transcript reader module's own landing page.
 *
 * "Recordings" (upload + library, SPEC-010) and "Transcript reader" (playback with word
 * highlighting, SPEC-012) are two different features that used to share one nav href,
 * which made both light up together no matter which you were on. The reader has no
 * standalone content of its own — reading only makes sense for a specific transcript —
 * so this page's whole job is to hand off to the most recently created readable one, or
 * explain why there isn't one yet.
 */
export default async function TranscriptReaderLandingPage() {
  const { transcripts } = await apiServerJson<{ transcripts: TranscriptSummary[] }>('/api/transcripts')
  const readable = transcripts.find((t) => t.counts.segments > 0)

  if (readable) redirect(`/dashboard/transcripts/${readable.id}`)

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Transcript reader</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Plays a recording back with the spoken word highlighted, and closes the grounding loop —
          every action item's citation opens here at the exact moment it was said.
        </p>
      </header>
      <GlassCard className="p-10">
        <EmptyState icon="bi-file-earmark-text" title="Nothing to read yet">
          Upload a recording first; once it has been transcribed, it opens here.
          <div className="mt-4">
            <Link href="/dashboard/transcripts">
              <Button variant="accent" icon="bi-cloud-arrow-up">
                Go to Recordings
              </Button>
            </Link>
          </div>
        </EmptyState>
      </GlassCard>
    </div>
  )
}
