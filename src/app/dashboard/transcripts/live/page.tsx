import { LiveMeetingCapture } from '@/components/transcripts/LiveMeetingCapture'

/** `/dashboard/transcripts/live` — SPEC-013. Client-only: media capture and the WebSocket
 * relay both need the browser. */
export default function LiveCapturePage() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4">
      <LiveMeetingCapture />
    </div>
  )
}
