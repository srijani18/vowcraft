import { PreferencesForm } from '@/components/settings/PreferencesForm'
import { apiServerJson } from '@/lib/api-server'
import type { SettingsView } from '@/server/settings/service'

export const dynamic = 'force-dynamic'

/** `/dashboard/settings` — the guardrail envelope (SPEC-005 §4.1). */
export default async function PreferencesPage() {
  const initial = await apiServerJson<SettingsView>('/api/settings')
  return <PreferencesForm initial={initial} />
}
