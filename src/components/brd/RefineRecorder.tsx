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

/**
 * Speak-again-to-amend, on an existing document — SPEC-014 §6.
 *
 * Distinct from the landing recorder: this one always refines, so its copy says so. A
 * control that looked like "record" on a page already showing a document would leave the
 * user unsure whether they were about to add to it or replace it — and the answer matters,
 * because one of those loses work.
 */
export function RefineRecorder({
  documentId,
  hasContent,
}: {
  documentId: string
  hasContent: boolean
}) {
  const live = useLiveTranscription()
  // Resolved here rather than passed in: it depends on a token only the browser holds.
  const [available, setAvailable] = useState<boolean | null>(null)
  const [reason, setReason] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function check() {
      if (!apiConfigured() || !accessToken()) {
        if (!cancelled) {
          setAvailable(false)
          setReason('Sign in again to enable voice capture — this feature now runs on the API backend.')
        }
        return
      }
      try {
        const body = await apiJson<{ available: boolean; reason: string | null }>('/api/speech/status')
        if (!cancelled) {
          setAvailable(body.available)
          setReason(body.reason)
        }
      } catch (err) {
        if (!cancelled) {
          setAvailable(false)
          setReason(err instanceof Error ? err.message : 'The API backend could not be reached.')
        }
      }
    }
    void check()
    return () => {
      cancelled = true
    }
  }, [])
  const router = useRouter()
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const recording = live.state === 'recording' || live.state === 'connecting' || live.state === 'stopping'

  const handleStop = useCallback(async () => {
    const spoken = await live.stop()
    if (spoken.trim().length < 10) {
      setFailure('That was too short to work with. Try a full sentence.')
      return
    }

    setBusy(true)
    setFailure(null)
    try {
      // Refine when there is something to refine; otherwise this is the first successful
      // generation for a document whose earlier attempt was interrupted or failed.
      const url = hasContent ? `/api/brd/${documentId}/refine` : '/api/brd'
      const response = await apiFetch(url, {
        method: 'POST',
        body: JSON.stringify({ spokenText: spoken }),
      })
      const body = (await response.json()) as unknown
      if (!response.ok) {
        setFailure(apiMessage(body, 'The document could not be updated.'))
        return
      }
      toast.push({ tone: 'success', title: hasContent ? 'Document revised' : 'Document generated' })
      if (!hasContent) {
        const created = body as { id?: string }
        if (created.id && created.id !== documentId) {
          router.push(`/dashboard/brd/${created.id}`)
          return
        }
      }
      router.refresh()
    } catch {
      setFailure('Could not reach the server. Nothing was changed.')
    } finally {
      setBusy(false)
    }
  }, [documentId, hasContent, live, router, toast])

  // Still asking — a neutral placeholder rather than flashing "unavailable" and correcting.
  if (available === null) {
    return (
      <GlassCard className="p-4">
        <p className="text-xs text-ink-muted">
          <i className="bi bi-arrow-repeat mr-1.5 animate-spin" aria-hidden />
          Checking transcription…
        </p>
      </GlassCard>
    )
  }

  if (!available) {
    return (
      <GlassCard className="border-warn/30 bg-warn/[0.05] p-4">
        <p className="text-xs text-warn">
          <i className="bi bi-mic-mute mr-1.5" aria-hidden />
          {reason ?? 'Live transcription is not configured, so this document cannot be added to by voice.'}
        </p>
        <Link href="/dashboard/settings/credentials" className="mt-2 inline-block">
          <Button variant="secondary" icon="bi-key-fill">
            Add a key
          </Button>
        </Link>
      </GlassCard>
    )
  }

  return (
    <GlassCard className={clsx('p-4 transition-colors', recording && 'border-danger/40 bg-danger/[0.04]')}>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => (live.state === 'recording' ? void handleStop() : void live.start())}
          disabled={busy || live.state === 'connecting' || live.state === 'stopping'}
          aria-label={live.state === 'recording' ? 'Stop and apply' : 'Record additional requirements'}
          className={clsx(
            'relative grid size-11 shrink-0 place-items-center rounded-full border transition-all',
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            'disabled:cursor-not-allowed disabled:opacity-50',
            live.state === 'recording'
              ? 'border-danger/50 bg-danger/15 text-danger'
              : 'border-cta-edge bg-cta text-cta-on',
          )}
        >
          {live.state === 'recording' && (
            <span
              className="absolute inset-0 animate-ping rounded-full bg-danger/20"
              style={{ animationDuration: '1.8s' }}
              aria-hidden
            />
          )}
          <i
            className={clsx('bi relative text-base', live.state === 'recording' ? 'bi-stop-fill' : 'bi-mic-fill')}
            aria-hidden
          />
        </button>

        <div className="min-w-0 flex-1" aria-live="polite">
          {busy ? (
            <p className="text-sm font-medium text-ink">
              <i className="bi bi-arrow-repeat mr-1.5 animate-spin" aria-hidden />
              {hasContent ? 'Working it into the document…' : 'Writing the document…'}
            </p>
          ) : recording ? (
            <p className="text-sm font-medium text-ink">
              {live.state === 'connecting' ? 'Connecting…' : live.state === 'stopping' ? 'Finishing…' : 'Listening'}
            </p>
          ) : (
            <>
              <p className="text-sm font-medium text-ink">
                {hasContent ? 'Add more requirements' : 'Record this document'}
              </p>
              <p className="mt-0.5 text-xs text-ink-muted">
                {hasContent
                  ? 'Existing requirements keep their ids and wording — only what you change changes.'
                  : 'The earlier attempt did not produce a document. Speak to generate one.'}
              </p>
            </>
          )}
        </div>
      </div>

      {recording && live.displayText && (
        <div className="mt-3 border-t border-edge/20 pt-3">
          <Badge tone="danger" icon="bi-broadcast">
            Transcribing
          </Badge>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
            {live.displayText}
            <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-accent align-middle" aria-hidden />
          </p>
        </div>
      )}

      {(failure ?? live.error) && (
        <p className="mt-3 rounded-lg border border-danger/40 bg-danger/[0.07] px-2.5 py-1.5 text-xs text-danger">
          {failure ?? live.error}
        </p>
      )}
    </GlassCard>
  )
}
