'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, apiJson, speechSocket } from '@/lib/api-client'
import { TranscriptAccumulator, pickMimeType } from '@/lib/speech-client'

/**
 * Browser tab audio → the existing live-transcription relay → a real transcript — SPEC-013.
 *
 * Reuses SPEC-014 §3's relay completely unmodified: audio bytes are audio bytes regardless
 * of whether they came from a microphone or a shared tab. What's different from
 * `useLiveTranscription.ts` is the media source (`getDisplayMedia`, not `getUserMedia`),
 * the stop signal (the browser's own "Stop sharing" control firing `track.onended`, not
 * only an in-app button), and what happens on stop — this posts the accumulated utterances
 * to `POST /api/transcripts/live` rather than to a BRD-generation endpoint, landing on an
 * ordinary `Transcript` row instead of a document.
 */

export type CaptureState = 'idle' | 'requesting-share' | 'connecting' | 'capturing' | 'finalizing'

export interface LiveMeetingCapture {
  state: CaptureState
  displayText: string
  elapsedMs: number
  error: string | null
  provider: string | null
  /** Whether this browser can share a tab's audio at all — checked once, up front, since
   * it's a hard capability gate rather than something worth discovering mid-attempt. */
  supported: boolean
  start: () => Promise<void>
  /** Resolves with the finalized transcript's id, or null if nothing was captured. */
  stop: () => Promise<string | null>
}

export function useLiveMeetingCapture(): LiveMeetingCapture {
  const [state, setState] = useState<CaptureState>('idle')
  const [displayText, setDisplayText] = useState('')
  const [elapsedMs, setElapsedMs] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [provider, setProvider] = useState<string | null>(null)

  const accumulator = useRef(new TranscriptAccumulator())
  const socket = useRef<WebSocket | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const displayStream = useRef<MediaStream | null>(null)
  const audioStream = useRef<MediaStream | null>(null)
  const captureStartedAt = useRef(0)
  const elapsedTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const everReady = useRef(false)

  const supported =
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices !== 'undefined' &&
    'getDisplayMedia' in navigator.mediaDevices

  const teardown = useCallback(() => {
    if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop()
    recorder.current = null
    audioStream.current?.getTracks().forEach((track) => track.stop())
    audioStream.current = null
    displayStream.current?.getTracks().forEach((track) => track.stop())
    displayStream.current = null
    socket.current?.close()
    socket.current = null
    if (elapsedTimer.current) clearInterval(elapsedTimer.current)
    elapsedTimer.current = null
  }, [])

  // Release the shared tab if the component unmounts mid-capture — otherwise the browser's
  // sharing indicator stays lit after navigating away.
  useEffect(() => teardown, [teardown])

  const start = useCallback(async () => {
    setError(null)
    accumulator.current.reset()
    setDisplayText('')
    setElapsedMs(0)
    everReady.current = false

    if (!supported) {
      setError('This browser can’t share tab audio. Try Chrome or Edge.')
      return
    }

    const mimeType = pickMimeType()
    if (!mimeType) {
      setError('This browser cannot record audio. Try Chrome or Edge.')
      return
    }

    setState('requesting-share')
    let display: MediaStream
    try {
      // `video: true` alongside `audio: true`: some Chrome versions do not reliably show
      // the tab picker for an audio-only request.
      display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })
    } catch (err) {
      const denied = (err as Error).name === 'NotAllowedError'
      setError(denied ? 'Sharing was cancelled.' : 'Could not start sharing a tab.')
      setState('idle')
      return
    }

    const audioTracks = display.getAudioTracks()
    const primaryAudioTrack = audioTracks[0]
    if (!primaryAudioTrack) {
      display.getTracks().forEach((track) => track.stop())
      setError('That share had no audio — try again and check “Share tab audio”.')
      setState('idle')
      return
    }

    // The video track is never rendered or sent anywhere; stop it immediately rather than
    // carry it for the capture's duration for nothing.
    display.getVideoTracks().forEach((track) => track.stop())
    displayStream.current = display
    const audioOnly = new MediaStream(audioTracks)
    audioStream.current = audioOnly

    const target = speechSocket()
    if (!target) {
      setError('Your session has expired. Reload the page and sign in again.')
      teardown()
      setState('idle')
      return
    }

    setState('connecting')
    const ws = new WebSocket(target.url, target.protocols)
    ws.binaryType = 'arraybuffer'
    socket.current = ws

    // The native "Stop sharing" control ends the audio track directly — this is the
    // equivalent of an upload finishing, so it drives the same stop() path a button would.
    primaryAudioTrack.onended = () => void stop()

    ws.onmessage = (event) => {
      let message: { type?: string; text?: string; isFinal?: boolean; message?: string; displayName?: string }
      try {
        message = JSON.parse(typeof event.data === 'string' ? event.data : '')
      } catch {
        return
      }

      if (message.type === 'ready') {
        everReady.current = true
        setProvider(message.displayName ?? null)
        captureStartedAt.current = performance.now()
        elapsedTimer.current = setInterval(() => setElapsedMs(performance.now() - captureStartedAt.current), 1000)

        const mediaRecorder = new MediaRecorder(audioOnly, { mimeType })
        recorder.current = mediaRecorder
        mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) ws.send(e.data)
        }
        mediaRecorder.start(250)
        setState('capturing')
        return
      }

      if (message.type === 'transcript' && typeof message.text === 'string') {
        const atMs = performance.now() - captureStartedAt.current
        accumulator.current.add({ text: message.text, final: Boolean(message.isFinal) }, atMs)
        setDisplayText(accumulator.current.displayText)
        return
      }

      if (message.type === 'error') {
        setError(message.message ?? 'Capture could not be started.')
        teardown()
        setState('idle')
      }
    }

    ws.onerror = () => {
      setError(
        everReady.current
          ? 'The connection dropped mid-capture. Anything captured so far is kept — stop and process it, or start again.'
          : 'Could not reach the transcription service. Check that the backend is running, then try again.',
      )
      teardown()
      setState('idle')
    }

    ws.onclose = () => {
      if (recorder.current) teardown()
      setState((current) => (current === 'capturing' || current === 'connecting' ? 'idle' : current))
    }
  }, [supported, teardown])

  const stop = useCallback(async (): Promise<string | null> => {
    if (state !== 'capturing' && state !== 'connecting') return null
    setState('finalizing')

    if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop()
    if (socket.current?.readyState === WebSocket.OPEN) {
      try {
        socket.current.send(JSON.stringify({ type: 'stop' }))
      } catch {
        // Already gone; the wait below is then a no-op.
      }
    }
    // Same rationale as useLiveTranscription.ts: the provider emits the final frame for an
    // utterance after the audio ends, so closing immediately loses the tail of it.
    await new Promise((resolve) => setTimeout(resolve, 900))

    const utterances = accumulator.current.utterances
    const durationMs = Math.round(performance.now() - captureStartedAt.current)
    teardown()

    if (utterances.length === 0) {
      setError('Nothing was captured. Share a tab that has audio and speak, then stop.')
      setState('idle')
      return null
    }

    try {
      const result = await apiJson<{ transcript: { id: string } }>('/api/transcripts/live', {
        method: 'POST',
        body: JSON.stringify({
          utterances: utterances.map((u) => ({ text: u.text, atMs: Math.round(u.atMs) })),
          durationMs,
          provider,
        }),
      })
      setState('idle')
      return result.transcript.id
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the capture.')
      setState('idle')
      return null
    }
  }, [state, provider, teardown])

  return { state, displayText, elapsedMs, error, provider, supported, start, stop }
}
