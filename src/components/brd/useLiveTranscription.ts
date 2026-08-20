'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { TranscriptAccumulator } from '@/lib/speech-client'
import { speechSocket } from '@/lib/api-client'

/**
 * Microphone → FastAPI → Deepgram, and the transcript back — SPEC-014 §3.
 *
 *     Browser ──WS(audio)──> FastAPI ──WS──> Deepgram
 *     Browser <──transcript── FastAPI <──partial──
 *
 * The relay is why this file is short. It previously had to know which auth mode a vendor
 * wanted, which container to record, and where the text sat in the vendor's JSON — and it
 * got the auth mode wrong, which presented as an unexplained dropped connection. None of
 * that is here now: the backend owns every provider detail and sends one normalised shape.
 *
 * Protocol, both directions:
 *
 *     → binary            audio, exactly as MediaRecorder produced it
 *     → {"type":"stop"}   stop capturing; flush the final utterance
 *     ← {"type":"ready", displayName, …}
 *     ← {"type":"transcript", text, isFinal}
 *     ← {"type":"error", code, message}
 *     ← {"type":"closed", reason, finalText}
 */

export type RecorderState = 'idle' | 'connecting' | 'recording' | 'stopping'

export interface LiveTranscription {
  state: RecorderState
  /** Finalised text plus the current interim guess — what to render while speaking. */
  displayText: string
  /** Finalised text only — what to generate from, never a half-heard guess. */
  finalText: string
  error: string | null
  provider: string | null
  start: () => Promise<void>
  stop: () => Promise<string>
}

/** What MediaRecorder should produce. Opus in WebM where available; the backend copes. */
function pickMimeType(): string | null {
  if (typeof MediaRecorder === 'undefined') return null
  for (const candidate of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate
  }
  return null
}

export function useLiveTranscription(): LiveTranscription {
  const [state, setState] = useState<RecorderState>('idle')
  const [displayText, setDisplayText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [provider, setProvider] = useState<string | null>(null)

  const accumulator = useRef(new TranscriptAccumulator())
  const socket = useRef<WebSocket | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  /*
   * Read at the moment of stopping, from a callback closed over an older render, so a ref
   * rather than state — what gets generated from must be the latest committed text.
   */
  const finalText = useRef('')
  /*
   * Whether the relay ever said `ready`. The only way to tell a *refused* session from a
   * *dropped* one: browsers hide handshake status codes, so `onerror` and `onclose` look
   * identical either way, and the two need completely different advice.
   */
  const everReady = useRef(false)

  const teardown = useCallback(() => {
    if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop()
    recorder.current = null
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
    socket.current?.close()
    socket.current = null
  }, [])

  // Release the microphone if the component unmounts mid-recording — otherwise the
  // browser's recording indicator stays lit after navigating away.
  useEffect(() => teardown, [teardown])

  const start = useCallback(async () => {
    setError(null)
    accumulator.current.reset()
    finalText.current = ''
    everReady.current = false
    setDisplayText('')
    setState('connecting')

    const target = speechSocket()
    if (!target) {
      setError('Your session has expired. Reload the page and sign in again.')
      setState('idle')
      return
    }

    const mimeType = pickMimeType()
    if (!mimeType) {
      setError('This browser cannot record audio. Try Chrome, Edge or Safari.')
      setState('idle')
      return
    }

    // Microphone before socket: a socket opened while a permission prompt sits on screen
    // is a connection held open for nothing, and the user may never grant it.
    let media: MediaStream
    try {
      media = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
    } catch (err) {
      const denied = (err as Error).name === 'NotAllowedError'
      setError(
        denied
          ? 'Microphone access was blocked. Allow it in your browser’s address bar, then try again.'
          : 'No microphone was available.',
      )
      setState('idle')
      return
    }
    stream.current = media

    const ws = new WebSocket(target.url, target.protocols)
    ws.binaryType = 'arraybuffer'
    socket.current = ws

    ws.onmessage = (event) => {
      let message: { type?: string; text?: string; isFinal?: boolean; message?: string; displayName?: string; finalText?: string }
      try {
        message = JSON.parse(typeof event.data === 'string' ? event.data : '')
      } catch {
        return
      }

      if (message.type === 'ready') {
        everReady.current = true
        setProvider(message.displayName ?? null)
        // Only start capturing once the upstream is confirmed, so no audio is recorded
        // into a connection that is about to be refused.
        const mediaRecorder = new MediaRecorder(media, { mimeType })
        recorder.current = mediaRecorder
        mediaRecorder.ondataavailable = (e) => {
          if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) ws.send(e.data)
        }
        mediaRecorder.start(250)
        setState('recording')
        return
      }

      if (message.type === 'transcript' && typeof message.text === 'string') {
        accumulator.current.add({ text: message.text, final: Boolean(message.isFinal) })
        finalText.current = accumulator.current.finalText
        setDisplayText(accumulator.current.displayText)
        return
      }

      if (message.type === 'error') {
        // The backend wrote this sentence for the user, naming the actual cause — an
        // invalid key, a provider refusal, an expired session.
        setError(message.message ?? 'Transcription could not be started.')
        teardown()
        setState('idle')
        return
      }

      if (message.type === 'closed') {
        // The relay's own accumulation wins if it has more: a frame we dropped would
        // otherwise leave a silent gap in the transcript.
        if (message.finalText && message.finalText.length > finalText.current.length) {
          finalText.current = message.finalText
          setDisplayText(message.finalText)
        }
      }
    }

    ws.onerror = () => {
      setError(
        everReady.current
          ? 'The transcription connection dropped mid-sentence. Anything already transcribed is kept — press record again to continue.'
          : 'Could not reach the transcription service. Check that the backend is running, then try again.',
      )
      teardown()
      setState('idle')
    }

    ws.onclose = () => {
      if (recorder.current) teardown()
      setState((current) => (current === 'recording' || current === 'connecting' ? 'idle' : current))
    }
  }, [teardown])

  const stop = useCallback(async (): Promise<string> => {
    setState('stopping')
    // Stop capturing at once, but leave the socket open briefly: the provider emits the
    // final frame for an utterance *after* the audio ends, and closing immediately loses
    // the tail of the closing sentence.
    if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop()
    if (socket.current?.readyState === WebSocket.OPEN) {
      try {
        socket.current.send(JSON.stringify({ type: 'stop' }))
      } catch {
        // Already gone; the wait below is then a no-op.
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 900))

    teardown()
    setState('idle')
    return finalText.current
  }, [teardown])

  return {
    state,
    displayText,
    get finalText() {
      return finalText.current
    },
    error,
    provider,
    start,
    stop,
  }
}
