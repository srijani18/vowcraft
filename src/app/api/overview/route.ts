import { currentUser } from '@/lib/auth'
import { handle } from '@/lib/http'
import { getOverview } from '@/server/analytics/service'

export const dynamic = 'force-dynamic'

/** GET /api/overview — the landing dashboard's data (SPEC-005 §7). */
export function GET(req: Request) {
  return handle(req, async () => {
    const user = await currentUser()
    return getOverview(user.id, user.email)
  })
}
