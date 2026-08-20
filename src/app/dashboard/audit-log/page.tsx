import { AuditLogViewer } from '@/components/audit/AuditLogViewer'
import { apiServerJson } from '@/lib/api-server'
import type { AuditResult } from '@/server/analytics/service'

export const dynamic = 'force-dynamic'

/** `/dashboard/audit-log` — SPEC-003 §7. Read-only; there is no write path. */
export default async function AuditLogPage({
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
  const initial = await apiServerJson<AuditResult>(`/api/audit-log${qs ? `?${qs}` : ''}`)
  return <AuditLogViewer initial={initial} />
}
