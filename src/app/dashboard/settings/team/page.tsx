import { TeamRoster } from '@/components/team/TeamRoster'
import { apiServerJson } from '@/lib/api-server'
import type { TeamMemberView } from '@/server/team/service'

export const dynamic = 'force-dynamic'

/** `/dashboard/settings/team` — the roster (SPEC-005 §4.2). */
export default async function TeamPage() {
  const { members } = await apiServerJson<{ members: TeamMemberView[] }>('/api/team')
  return <TeamRoster initial={members} />
}
