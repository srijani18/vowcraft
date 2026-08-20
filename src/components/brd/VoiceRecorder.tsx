'use client'

import { useRouter } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import Link from 'next/link'
import { Badge, Button, GlassCard } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { apiMessage } from '@/lib/api-error'
import { accessToken, apiConfigured, apiFetch, apiJson } from '@/lib/api-client'
import { useLiveTranscription } from './useLiveTranscription'
import { BrdView } from './BrdView'
import { BrdExportLink } from './BrdExportLink'
import type { Brd } from '@/server/brd/schema'

/**
 * The voice-to-BRD surface — SPEC-014 §2.
 *
 * Four states, and the record button's own position is the state indicator: centred and
 * large when idle, small and top-centre while recording. A user who has just pressed it
 * sees the control they pressed move and change shape, so there is never a question of
 * whether recording started.
 *
 * There is deliberately no text input. This is a voice surface; a textarea invites typing,
 * at which point the streaming ASR is dead weight and the product is a worse chat window.
 */

export interface SpeechStatusView {
  available: boolean
  displayName: string | null
  model: string | null
  selfHosted: boolean
  reason: string | null
}

interface DocumentView {
  id: string
  title: string
  content: Brd | null
  revisionCount: number
  lastError: string | null
}

type Phase = 'idle' | 'recording' | 'generating' | 'ready'

export function VoiceRecorder({
  initialDocument,
}: {
  /** Set when resuming an interrupted or existing document (SPEC-014 §4.3). */
  initialDocument?: DocumentView | null
}) {
  const live = useLiveTranscription()
  /*
   * Resolved from the backend on the client, not passed down from a server component.
   *
   * The status depends on a credential the *backend* holds and on a bearer token the
   * browser holds, and a server component has neither — it cannot see sessionStorage. So
   * the check happens where both are available. `null` means "still asking", which the UI
   * renders as a neutral state rather than flashing "not configured" and correcting itself.
   */
  const [status, setStatus] = useState<SpeechStatusView | null>(null)

  useEffect(() => {
    let cancelled = false
    async function resolveStatus() {
      if (!apiConfigured()) {
        setStatus({
          available: false, displayName: null, model: null, selfHosted: false,
          reason: 'NEXT_PUBLIC_API_URL is not set, so the frontend does not know where the backend is.',
        })
        return
      }
      if (!accessToken()) {
        setStatus({
          available: false, displayName: null, model: null, selfHosted: false,
          reason: 'Sign in again to enable voice capture — this feature now runs on the API backend.',
        })
        return
      }
      try {
        const body = await apiJson<SpeechStatusView>('/api/speech/status')
        if (!cancelled) setStatus(body)
      } catch (err) {
        if (!cancelled) {
          setStatus({
            available: false, displayName: null, model: null, selfHosted: false,
            reason:
              err instanceof Error && err.message
                ? err.message
                : 'The API backend could not be reached.',
          })
        }
      }
    }
    void resolveStatus()
    return () => {
      cancelled = true
    }
  }, [])
  const toast = useToast()
  const router = useRouter()

  const [document, setDocument] = useState<DocumentView | null>(initialDocument ?? null)
  const [generating, setGenerating] = useState(false)
  const [frozenTranscript, setFrozenTranscript] = useState('')
  const [failure, setFailure] = useState<string | null>(null)

  const recording = live.state === 'recording' || live.state === 'connecting' || live.state === 'stopping'
  const phase: Phase = generating ? 'generating' : recording ? 'recording' : document ? 'ready' : 'idle'
  const refining = Boolean(document?.content)

  /** Announced to screen readers; also the visible caption under the button. */
  const stateLabel =
    live.state === 'connecting'
      ? 'Connecting…'
      : live.state === 'recording'
        ? 'Listening'
        : live.state === 'stopping'
          ? 'Finishing…'
          : generating
            ? refining ? 'Revising the document…' : 'Writing the document…'
            : null

  const submit = useCallback(
    async (spokenText: string) => {
      setGenerating(true)
      setFailure(null)
      try {
        const url = refining ? `/api/brd/${document!.id}/refine` : '/api/brd'
        const response = await apiFetch(url, {
          method: 'POST',
          body: JSON.stringify({ spokenText }),
        })
        const body = (await response.json()) as unknown
        if (!response.ok) {
          setFailure(apiMessage(body, 'The document could not be generated.'))
          return
        }
        const next = body as DocumentView
        setDocument(next)
        setFrozenTranscript('')
        toast.push({
          tone: 'success',
          title: refining ? 'Document revised' : 'Document generated',
          detail: refining ? `Revision ${next.revisionCount}` : next.title,
        })
        // So the history module and the sidebar counts reflect it.
        router.refresh()
      } catch {
        setFailure('Could not reach the server. Your transcript is still on screen.')
      } finally {
        setGenerating(false)
      }
    },
    [document, refining, router, toast],
  )

  const handleStop = useCallback(async () => {
    const spoken = await live.stop()
    /*
     * Frozen before generating, so the transcript stays on screen through the model call —
     * SPEC-014 §2. Clearing it here would leave the user watching a spinner with no record
     * of what they just said.
     */
    setFrozenTranscript(spoken)
    if (spoken.trim().length < 10) {
      setFailure('That was too short to write requirements from. Try describing the need in a sentence or two.')
      return
    }
    await submit(spoken)
  }, [live, submit])

  // Space toggles recording, so the primary action is reachable without a pointer
  // (SPEC-014 §9.12). Ignored while typing in a field, and while generating.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || generating) return
      const target = event.target as HTMLElement | null
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
      if (target?.isContentEditable) return
      event.preventDefault()
      if (live.state === 'recording') void handleStop()
      else if (live.state === 'idle' && status?.available) void live.start()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [generating, handleStop, live, status?.available])

  const transcript = recording ? live.displayText : frozenTranscript

  return (
    <div className="space-y-6">
      {/* ── the record control ─────────────────────────────────────────────── */}
      <div
        className={clsx(
          'flex flex-col items-center transition-all duration-500',
          phase === 'idle' ? 'justify-center py-16 sm:py-24' : 'py-4',
        )}
      >
        {status === null ? (
          <p className="text-sm text-ink-muted">
            <i className="bi bi-arrow-repeat mr-1.5 animate-spin" aria-hidden />
            Checking transcription…
          </p>
        ) : !status.available ? (
          <UnavailableNotice reason={status.reason} />
        ) : (
          <>
            <button
              type="button"
              onClick={() => (live.state === 'recording' ? void handleStop() : void live.start())}
              disabled={generating || live.state === 'connecting' || live.state === 'stopping'}
              aria-label={live.state === 'recording' ? 'Stop recording' : 'Start recording'}
              className={clsx(
                'group relative grid place-items-center rounded-full border transition-all duration-500',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent',
                'disabled:cursor-not-allowed disabled:opacity-60',
                phase === 'idle'
                  ? 'size-32 border-cta-edge bg-cta text-cta-on glow-cta sm:size-40'
                  : 'size-14 border-danger/50 bg-danger/15 text-danger',
              )}
            >
              {/* The pulse is the only thing on screen that says "audio is being
                  captured right now", so it is tied to the recording state alone. */}
              {live.state === 'recording' && (
                <span
                  className="absolute inset-0 animate-ping rounded-full bg-danger/20"
                  style={{ animationDuration: '1.8s' }}
                  aria-hidden
                />
              )}
              <i
                className={clsx(
                  'bi relative',
                  live.state === 'recording' ? 'bi-stop-fill text-xl' : 'bi-mic-fill',
                  phase === 'idle' ? 'text-4xl sm:text-5xl' : 'text-xl',
                )}
                aria-hidden
              />
            </button>

            <div aria-live="polite" className="mt-4 text-center">
              {stateLabel ? (
                <p className="text-sm font-medium text-ink">{stateLabel}</p>
              ) : phase === 'idle' ? (
                <>
                  <p className="text-base font-medium text-ink">Describe what you need built</p>
                  <p className="mt-1 max-w-md text-sm text-ink-muted">
                    Speak naturally. It is transcribed as you talk, then written up as a business
                    requirements document.
                  </p>
                </>
              ) : (
                <p className="text-sm text-ink-muted">
                  {refining ? 'Speak again to add requirements' : 'Press to record'}
                </p>
              )}
              {phase === 'idle' && (
                <p className="mt-2 text-xs text-ink-faint">
                  <kbd className="rounded border border-edge/30 px-1.5 py-0.5">Space</kbd> to start
                  {status?.displayName && ` · ${status.displayName}`}
                  {status?.selfHosted && ' · audio stays on your network'}
                </p>
              )}
            </div>
          </>
        )}
      </div>

      {/* ── live / frozen transcript ───────────────────────────────────────── */}
      {(transcript || recording) && (
        <GlassCard className="p-4 sm:p-5">
          <div className="mb-2.5 flex items-center gap-2">
            <Badge tone={recording ? 'danger' : 'muted'} icon={recording ? 'bi-broadcast' : 'bi-soundwave'}>
              {recording ? 'Transcribing' : 'What you said'}
            </Badge>
            {live.provider && <span className="text-xs text-ink-faint">{live.provider}</span>}
          </div>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
            {transcript || <span className="text-ink-faint">Listening…</span>}
            {live.state === 'recording' && (
              <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-accent align-middle" aria-hidden />
            )}
          </p>
        </GlassCard>
      )}

      {live.error && <Notice tone="warn" icon="bi-exclamation-triangle-fill" message={live.error} />}
      {failure && <Notice tone="danger" icon="bi-x-octagon-fill" message={failure} />}

      {/* ── the document ──────────────────────────────────────────────────── */}
      {generating && <GeneratingSkeleton refining={refining} />}

      {document?.content && !generating && (
        <GlassCard className="p-5 sm:p-6">
          <div className="mb-5 flex flex-wrap items-start justify-between gap-3 border-b border-edge/20 pb-4">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold tracking-tight text-ink">{document.content.title}</h2>
              <p className="mt-1 text-xs text-ink-faint">
                {document.revisionCount} revision{document.revisionCount === 1 ? '' : 's'} · speak again to
                refine in place
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <Link href={`/dashboard/brd/${document.id}`}>
                <Button variant="secondary" icon="bi-clock-history">
                  History
                </Button>
              </Link>
              <BrdExportLink documentId={document.id} format="md" label="Markdown" icon="bi-download" />
            </div>
          </div>
          <BrdView document={document.content} />
        </GlassCard>
      )}
    </div>
  )
}

function Notice({ tone, icon, message }: { tone: 'warn' | 'danger'; icon: string; message: string }) {
  return (
    <p
      className={clsx(
        'rounded-xl border px-3 py-2.5 text-xs',
        tone === 'warn' ? 'border-warn/40 bg-warn/[0.08] text-warn' : 'border-danger/40 bg-danger/[0.08] text-danger',
      )}
    >
      <i className={clsx('bi mr-1.5', icon)} aria-hidden />
      {message}
    </p>
  )
}

/** States what is happening rather than showing an unlabelled spinner. */
function GeneratingSkeleton({ refining }: { refining: boolean }) {
  return (
    <GlassCard className="p-5 sm:p-6">
      <div className="flex items-center gap-2.5">
        <i className="bi bi-arrow-repeat animate-spin text-accent" aria-hidden />
        <p className="text-sm font-medium text-ink">
          {refining ? 'Working your new requirements into the document' : 'Writing the requirements document'}
        </p>
      </div>
      <p className="mt-1.5 text-xs text-ink-muted">
        {refining
          ? 'Existing requirements keep their ids and wording; only what you changed changes.'
          : 'Anything it cannot infer from what you said becomes an open question rather than an invention.'}
      </p>
      <div className="mt-4 space-y-2" aria-hidden>
        {[100, 92, 78, 96, 64].map((width, i) => (
          <div
            key={i}
            className="h-3 animate-pulse rounded bg-ink/10"
            style={{ width: `${width}%`, animationDelay: `${i * 120}ms` }}
          />
        ))}
      </div>
    </GlassCard>
  )
}

/**
 * The disabled state, with its reason — SPEC-014 §3.2.
 *
 * A button that looks ready and fails on click is worse than one that explains itself.
 * There is no mock fallback: a simulated live transcript would be indistinguishable on
 * screen from a real one.
 */
function UnavailableNotice({ reason }: { reason: string | null }) {
  return (
    <GlassCard className="max-w-lg border-warn/30 bg-warn/[0.05] p-6 text-center">
      <div className="mx-auto grid size-14 place-items-center rounded-full border border-warn/40 bg-warn/10">
        <i className="bi bi-mic-mute text-2xl text-warn" aria-hidden />
      </div>
      <h2 className="mt-4 text-base font-semibold text-ink">Live transcription is not configured</h2>
      <p className="mt-2 text-sm leading-relaxed text-ink-muted">
        {reason ?? 'No streaming transcription provider is available on this deployment.'}
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
