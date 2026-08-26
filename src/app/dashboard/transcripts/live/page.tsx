import { LiveMeetingCapture } from '@/components/transcripts/LiveMeetingCapture'

/*
 * Every other page under /dashboard declares this, and this one was the sole exception —
 * an oversight, not a distinction. Without it Next.js prerenders the route at build time,
 * which runs the dashboard layout's server code (it queries the signed-in user) and
 * validates the environment. A build machine has no runtime secrets, so `next build`
 * failed with "Invalid environment" on whichever variable that host left unset.
 *
 * Static rendering was never right here regardless: the page sits under an authenticated,
 * per-user layout, and its own content needs the browser for media capture.
 */
export const dynamic = 'force-dynamic'

/** `/dashboard/transcripts/live` — SPEC-013. Client-only: media capture and the WebSocket
 * relay both need the browser. */
export default function LiveCapturePage() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4">
      <LiveMeetingCapture />
    </div>
  )
}
