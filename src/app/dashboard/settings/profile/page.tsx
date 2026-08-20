import { ProfileManager } from '@/components/profile/ProfileManager'
import { apiServerJson } from '@/lib/api-server'
import type { ProfileView } from '@/server/profile/service'

export const dynamic = 'force-dynamic'

/** `/dashboard/settings/profile` — SPEC-005 §3, §4, §8. */
export default async function ProfilePage() {
  const initial = await apiServerJson<ProfileView>('/api/profile')
  return <ProfileManager initial={initial} />
}
