import { Suspense } from 'react'
import { Board } from '@/components/action-items/Board'
import { GlassCard, Skeleton } from '@/components/ui/primitives'
import { apiServerJson } from '@/lib/api-server'
import type { ListResult } from '@/server/action-items/service'

export const dynamic = 'force-dynamic'

/**
 * `/dashboard/action-items` — SPEC-001 §3.
 *
 * The first page is fetched server-side from the FastAPI backend (SPEC-015 §7), so
 * first paint needs no client round-trip; the client component takes over for
 * filtering and mutations from there. FastAPI parses the query string itself — no
 * need to replicate that parsing here, just forward it.
 */
export default async function ActionItemsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const params = await searchParams
  const url = new URL('http://internal/action-items')
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((v) => url.searchParams.append(key, v))
    else if (value !== undefined) url.searchParams.set(key, value)
  }

  return (
    <Suspense fallback={<BoardSkeleton />}>
      <BoardLoader search={url.searchParams.toString()} />
    </Suspense>
  )
}

async function BoardLoader({ search }: { search: string }) {
  const initial = await apiServerJson<ListResult>(`/api/action-items${search ? `?${search}` : ''}`)
  return <Board initial={initial} initialSearch={search} />
}

function BoardSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-56" />
      <GlassCard className="p-4">
        <Skeleton className="h-9 w-full" />
      </GlassCard>
      {[0, 1, 2, 3].map((n) => (
        <Skeleton key={n} className="h-32 w-full rounded-2xl" />
      ))}
    </div>
  )
}
