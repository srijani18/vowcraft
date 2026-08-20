import { notFound } from 'next/navigation'
import { TranscriptReader } from '@/components/transcripts/TranscriptReader'
import { ApiServerError, apiServerJson } from '@/lib/api-server'
import type { TranscriptDetail } from '@/server/ingest/service'

export const dynamic = 'force-dynamic'

/**
 * `/dashboard/transcripts/:id` — SPEC-012 §2.
 *
 * `?t=<ms>` opens at a moment and `?q=` with a search applied; both are what the deep
 * links from an action item's citation use.
 */
export default async function TranscriptPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>
  searchParams: Promise<{ t?: string; q?: string }>
}) {
  const { id } = await params
  const { t, q } = await searchParams

  const transcript = await apiServerJson<TranscriptDetail>(`/api/transcripts/${id}`).catch((err) => {
    if (err instanceof ApiServerError && err.status === 404) return null
    throw err
  })
  if (!transcript) notFound()

  // A malformed or absent offset opens at the start rather than erroring.
  const parsed = Number(t)
  const initialAtMs = Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : undefined

  return <TranscriptReader transcript={transcript} initialAtMs={initialAtMs} initialQuery={q} />
}
