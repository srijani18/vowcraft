import { Overview } from '@/components/overview/Overview'
import { currentUser } from '@/lib/auth'
import { getOverview } from '@/server/analytics/service'

export const dynamic = 'force-dynamic'

/**
 * `/dashboard/overview` — the analytics landing (SPEC-005 §7).
 *
 * Moved off `/dashboard` when the voice surface took the landing slot (SPEC-014 §2). An
 * overview is what you read *after* doing work; putting it where the user lands made the
 * tool's own primary act something you had to go and find.
 */
export default async function OverviewPage() {
  const user = await currentUser()
  const data = await getOverview(user.id, user.email)
  return <Overview data={data} userName={user.name} />
}
