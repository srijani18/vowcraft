'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Badge, Button, GlassCard } from '@/components/ui/primitives'
import { accessToken, apiConfigured, apiJson } from '@/lib/api-client'
import type { SpeechStatusView } from '@/components/brd/VoiceRecorder'
import { useLiveMeetingCapture } from './useLiveMeetingCapture'

/**
 * "Capture a live meeting" — SPEC-013.
 *
 * Share a browser tab that has a meeting's audio (Meet, Teams, Zoom, or anything else
 * running in a tab), speak, then stop — the result lands as an ordinary transcript on
 * `/dashboard/transcripts/{id}`, same as an upload. No bot joins the call: this is
 * vendor-agnostic tab-audio capture, not an integration with any specific meeting tool.
 */

function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

export function LiveMeetingCapture() {
  const router = useRouter()
  const capture = useLiveMeetingCapture()
  const [status, setStatus] = useState<SpeechStatusView | null>(null)
  const [routing, setRouting] = useState(false)

  // Same reasoning as VoiceRecorder.tsx: availability depends on a backend credential and
  // a browser-held bearer token, neither of which a server component can see.
  useEffect(() => {
    let cancelled = false
    async function resolveStatus() {
      if (!apiConfigured()) {
        setStatus({ available: false, displayName: null, model: null, selfHosted: false, reason: 'NEXT_PUBLIC_API_URL is not set.' })
        return
      }
      if (!accessToken()) {
        setStatus({ available: false, displayName: null, model: null, selfHosted: false, reason: 'Sign in again to enable live capture.' })
        return
      }
      try {
        const body = await apiJson<SpeechStatusView>('/api/speech/status')
        if (!cancelled) setStatus(body)
      } catch (err) {
        if (!cancelled) {
          setStatus({
            available: false, displayName: null, model: null, selfHosted: false,
            reason: err instanceof Error && err.message ? err.message : 'The API backend could not be reached.',
          })
        }
      }
    }
    void resolveStatus()
    return () => {
      cancelled = true
    }
  }, [])

  const handleStop = async () => {
    const transcriptId = await capture.stop()
    if (transcriptId) {
      setRouting(true)
      router.push(`/dashboard/transcripts/${transcriptId}`)
    }
  }

  if (status && !status.available) {
    return (
      <GlassCard className="max-w-lg border-warn/30 bg-warn/[0.05] p-6 text-center">
        <div className="mx-auto grid size-14 place-items-center rounded-full border border-warn/40 bg-warn/10">
          <i className="bi bi-camera-video-off text-2xl text-warn" aria-hidden />
        </div>
        <h2 className="mt-4 text-base font-semibold text-ink">Live capture is not configured</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-muted">
          {status.reason ?? 'No streaming transcription provider is available on this deployment.'}
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          <Link href="/dashboard/settings/credentials">
            <Button variant="primary" icon="bi-key-fill">
              Add a key
            </Button>
          </Link>
          <Link href="/dashboard/transcripts">
            <Button variant="secondary" icon="bi-cloud-arrow-up">
              Upload a recording instead
            </Button>
          </Link>
        </div>
      </GlassCard>
    )
  }

  if (!capture.supported) {
    return (
      <GlassCard className="max-w-lg border-warn/30 bg-warn/[0.05] p-6 text-center">
        <h2 className="text-base font-semibold text-ink">This browser can’t share tab audio</h2>
        <p className="mt-2 text-sm text-ink-muted">Try Chrome or Edge on a desktop.</p>
      </GlassCard>
    )
  }

  const capturing = capture.state === 'capturing' || capture.state === 'connecting'

  return (
    <GlassCard className="max-w-lg p-6 text-center">
      <div className="mx-auto grid size-14 place-items-center rounded-full border border-accent-fill/40 bg-accent-fill/10">
        <i className={`bi ${capturing ? 'bi-record-circle text-danger' : 'bi-camera-video'} text-2xl`} aria-hidden />
      </div>

      {capturing && (
        <div className="mt-3 flex items-center justify-center gap-2">
          <Badge tone="danger" icon="bi-record-circle">
            Capturing
          </Badge>
          <span className="mono-num text-sm text-ink-muted">{formatElapsed(capture.elapsedMs)}</span>
        </div>
      )}

      {capture.state === 'idle' && (
        <>
          <h2 className="mt-4 text-base font-semibold text-ink">Capture a live meeting</h2>
          <p className="mt-2 text-sm text-ink-muted">
            Share a browser tab with a meeting's audio — Meet, Teams, Zoom, or anything else — and this
            becomes a normal recording once you stop.
          </p>
          <Button variant="primary" icon="bi-camera-video" onClick={capture.start} className="mt-4">
            Share a tab to capture
          </Button>
        </>
      )}

      {(capture.state === 'requesting-share' || capture.state === 'connecting') && (
        <p className="mt-4 text-sm text-ink-muted">Connecting…</p>
      )}

      {capture.state === 'capturing' && (
        <>
          <p className="mt-4 min-h-12 text-sm text-ink-muted">{capture.displayText || 'Listening…'}</p>
          <Button variant="danger" icon="bi-stop-fill" onClick={handleStop} className="mt-4" loading={routing}>
            Stop and process
          </Button>
          <p className="mt-2 text-[11px] text-ink-faint">
            Or use the browser's own "Stop sharing" control.
          </p>
        </>
      )}

      {capture.state === 'finalizing' && <p className="mt-4 text-sm text-ink-muted">Saving…</p>}

      {capture.error && <p className="mt-4 text-sm text-danger">{capture.error}</p>}

      <p className="mt-5 text-[11px] text-ink-faint">
        No diarization or video in this first version — just the spoken words, timestamped and
        extracted into action items.
      </p>
    </GlassCard>
  )
}
