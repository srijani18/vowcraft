import { SearchView } from '@/components/search/SearchView'
import { apiServerJson } from '@/lib/api-server'
import type { CredentialView } from '@/lib/credentials/types'

export const dynamic = 'force-dynamic'

/**
 * `/dashboard/search` — SPEC-021.
 *
 * Checks the `voyage` credential's own `configured` flag, not the EMBEDDING module's
 * aggregate status: `resolve_module("EMBEDDING")` always reports "live" because of the
 * unimplemented `local_bge` catalog entry (the same trap the credentials migration found
 * and had to route around on the backend — see SPEC-015 §7). Only the specific provider
 * this feature actually calls tells the truth about whether a search will work.
 */
export default async function SearchPage() {
  const { credentials } = await apiServerJson<{ credentials: CredentialView[] }>('/api/credentials')
  const voyageConfigured = credentials.some((c) => c.service === 'voyage' && c.configured)

  return <SearchView voyageConfigured={voyageConfigured} />
}
