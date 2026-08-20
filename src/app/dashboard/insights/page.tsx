import { InsightsView } from '@/components/insights/InsightsView'
import { apiServerJson } from '@/lib/api-server'
import type { DecisionsResult } from '@/server/insights/service'
import type { TranscriptSummary } from '@/server/ingest/service'

export const dynamic = 'force-dynamic'

/** `/dashboard/insights` — SPEC-020. Read-only; there is no write path. */
export default async function InsightsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const params = await searchParams
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((v) => search.append(key, v))
    else if (value !== undefined) search.set(key, value)
  }
  const qs = search.toString()

  const [initial, transcripts] = await Promise.all([
    apiServerJson<DecisionsResult>(`/api/decisions${qs ? `?${qs}` : ''}`),
    apiServerJson<{ transcripts: TranscriptSummary[] }>('/api/transcripts'),
  ])

  const recentSummaries = transcripts.transcripts.filter((t) => t.summary).slice(0, 8)

  return <InsightsView initial={initial} recentSummaries={recentSummaries} />
}
