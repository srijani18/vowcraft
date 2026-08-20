'use client'

import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Badge, Button, GlassCard } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { apiFetch, apiJson } from '@/lib/api-client'
import { apiMessage } from '@/lib/api-error'

/**
 * Upload and pipeline progress — SPEC-010 §5.
 *
 * The upload returns 202 and processing continues server-side, so this polls
 * `GET /api/transcripts/:id` until the stage reaches `done`. Progress comes from the
 * row rather than being animated locally: a fake progress bar that keeps moving while
 * the server has failed is worse than no progress bar.
 */

export interface PipelineStatus {
  transcribe: { available: boolean; provider: string | null; candidates: Candidate[] }
  extract: { available: boolean; provider: string | null; candidates: Candidate[] }
  ready: boolean
}

export interface Candidate {
  service: string
  displayName: string
  freeTier: boolean
  configured: boolean
}

interface TranscriptState {
  id: string
  title: string
  status: string
  stage: string
  progress: number
  transcribeError: string | null
  extractError: string | null
  extractErrorCode: string | null
  extractModel: string | null
  transcribeProvider: string | null
  extractProvider: string | null
  diarized: boolean
  language: string | null
  durationMs: number | null
  counts: { segments: number; actionItems: number; decisions: number }
}

const STAGES = [
  { key: 'uploaded', label: 'Uploaded', icon: 'bi-cloud-check' },
  { key: 'transcribing', label: 'Extracting audio, then transcribing', icon: 'bi-soundwave' },
  { key: 'extracting', label: 'Extracting actions', icon: 'bi-diagram-3' },
  { key: 'done', label: 'Done', icon: 'bi-check-circle' },
] as const

const ACCEPT = '.mp3,.m4a,.wav,.webm,.ogg,.flac,.mp4,.mov,.mkv,.avi,audio/*,video/*'
/** Audio goes to the provider as-is; video has its track extracted first. */
const MAX_AUDIO_MB = 25
const MAX_VIDEO_MB = 500

function isVideo(file: File): boolean {
  return file.type.startsWith('video/') || /\.(mp4|mov|mkv|avi|webm)$/i.test(file.name)
}

export function Uploader({ initialStatus }: { initialStatus: PipelineStatus }) {
  const router = useRouter()
  const toast = useToast()
  const inputRef = useRef<HTMLInputElement>(null)

  const [status] = useState(initialStatus)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [job, setJob] = useState<TranscriptState | null>(null)
  const [error, setError] = useState<string | null>(null)

  // ── poll while a job is in flight
  useEffect(() => {
    if (!job || job.stage === 'done') return
    const timer = setInterval(async () => {
      try {
        const response = await apiFetch(`/api/transcripts/${job.id}`)
        if (!response.ok) return
        const next = (await response.json()) as TranscriptState
        setJob(next)
        if (next.stage === 'done') {
          // Refresh the server components so the board and the sidebar counts update.
          router.refresh()
        }
      } catch {
        /* a dropped poll is not worth surfacing; the next one will land */
      }
    }, 1_500)
    return () => clearInterval(timer)
  }, [job, router])

  const upload = useCallback(
    async (file: File) => {
      setError(null)

      // Checked here purely to fail fast with a better message than a 422 round trip.
      const limit = isVideo(file) ? MAX_VIDEO_MB : MAX_AUDIO_MB
      if (file.size > limit * 1024 * 1024) {
        setError(
          `That file is ${(file.size / 1024 / 1024).toFixed(1)} MB, over the ${limit} MB limit for ` +
            (isVideo(file)
              ? 'video. Trim it, or export the audio track and upload that.'
              : 'audio. Try a shorter recording or a lower bitrate.'),
        )
        return
      }

      setUploading(true)
      const form = new FormData()
      form.append('file', file)

      try {
        const response = await apiFetch('/api/transcripts', { method: 'POST', body: form })
        const json = (await response.json()) as {
          ok?: boolean
          transcript?: { id: string; title: string; reused: boolean }
          message?: string
          error?: { message: string }
        }

        if (!response.ok || !json.ok || !json.transcript) {
          setError(json.error?.message ?? 'The upload failed. Please try again.')
          return
        }

        toast.push({
          tone: json.transcript.reused ? 'info' : 'success',
          title: json.transcript.reused ? 'Already uploaded' : 'Upload accepted',
          detail: json.message,
        })

        const detail = await apiJson<TranscriptState>(`/api/transcripts/${json.transcript.id}`)
        setJob(detail)
      } catch {
        setError('Could not reach the server. Check your connection and try again.')
      } finally {
        setUploading(false)
      }
    },
    [toast],
  )

  const activeIndex = STAGES.findIndex((s) => s.key === job?.stage)
  const transcribeFailed = job?.status === 'FAILED' || Boolean(job?.transcribeError)
  const extractFailed = Boolean(job?.extractError)
  /*
   * Either failure means the run did not complete, even though `stage` reads 'done'.
   * An earlier version keyed the tick marks off `stage` alone, so a transcript whose
   * extraction had failed showed "Extracting actions ✓  Done ✓" directly above the
   * error explaining that extraction had failed. Two claims, one of them false.
   */
  const failed = transcribeFailed || extractFailed
  const extractionSucceeded = job?.stage === 'done' && !failed

  return (
    <div className="space-y-6">
      {/* ── provider readiness, before anyone wastes an upload */}
      {!status.ready && (
        <GlassCard className="border-warn/45 p-4">
          <p className="flex items-start gap-2.5 text-sm">
            <i className="bi bi-key-fill mt-0.5 shrink-0 text-warn" aria-hidden />
            <span>
              <span className="font-medium text-warn">Action extraction needs an API key.</span>{' '}
              <span className="text-ink-muted">
                Transcription can run on the bundled sample without one, but turning a transcript into
                action items needs a model. Groq and Gemini are both free and either covers it.
              </span>
            </span>
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {status.extract.candidates
              .filter((c) => c.freeTier)
              .map((c) => (
                <Badge key={c.service} tone={c.configured ? 'success' : 'muted'} icon={c.configured ? 'bi-check-lg' : 'bi-dash'}>
                  {c.displayName}
                </Badge>
              ))}
          </div>
          <Link href="/dashboard/settings/credentials" className="mt-3 inline-block">
            <Button variant="primary" icon="bi-key">
              Add a free key
            </Button>
          </Link>
        </GlassCard>
      )}

      {status.ready && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          <Badge tone="success" icon="bi-soundwave">
            {status.transcribe.provider ?? 'Bundled sample'}
          </Badge>
          <Badge tone="success" icon="bi-diagram-3">
            {status.extract.provider}
          </Badge>
          <span>ready to process a recording</span>
        </p>
      )}

      {/* ── the drop zone */}
      {!job && (
        <GlassCard
          className={clsx(
            'border-2 border-dashed p-8 text-center transition-colors sm:p-12',
            dragging ? 'border-accent-fill bg-accent-fill/[0.06]' : 'border-edge/30',
          )}
          lit={false}
        >
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              const file = e.dataTransfer.files?.[0]
              if (file) void upload(file)
            }}
          >
            <i
              className={clsx('bi text-4xl', uploading ? 'bi-arrow-repeat animate-spin text-accent' : 'bi-cloud-arrow-up text-accent')}
              aria-hidden
            />
            <h2 className="mt-4 text-base font-semibold">
              {uploading ? 'Uploading…' : 'Drop a recording here'}
            </h2>
            <p className="mx-auto mt-2 max-w-md text-sm text-ink-muted">
              Audio — MP3, M4A, WAV, WebM, OGG, FLAC — up to {MAX_AUDIO_MB} MB. Or video — MP4, MOV,
              MKV, AVI — up to {MAX_VIDEO_MB} MB, whose audio track is extracted here before anything
              is sent.
            </p>

            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="sr-only"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void upload(file)
              }}
            />
            <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
              <Button variant="primary" icon="bi-folder2-open" onClick={() => inputRef.current?.click()} loading={uploading}>
                Choose a file
              </Button>
            </div>

            <p className="mt-4 text-[11px] text-ink-faint">
              <i className="bi bi-shield-lock mr-1" aria-hidden />
              The file is stored in your own database. For video, only the extracted audio is sent to
              the transcription provider — the picture never leaves your machine.
            </p>
          </div>
        </GlassCard>
      )}

      {error && (
        <p role="alert" className="flex items-start gap-2 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2.5 text-sm text-danger">
          <i className="bi bi-exclamation-triangle-fill mt-0.5 shrink-0" aria-hidden />
          {error}
        </p>
      )}

      {/* ── progress */}
      {job && (
        <GlassCard className="lit-edge p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold">{job.title}</h2>
              <p className="mt-0.5 text-xs text-ink-faint">
                {job.language ? `${job.language.toUpperCase()} · ` : ''}
                {job.durationMs ? `${Math.round(job.durationMs / 60_000)} min · ` : ''}
                {job.counts.segments} segments
              </p>
            </div>
            <Badge tone={failed ? 'danger' : job.stage === 'done' ? 'success' : 'accent'}>
              {failed ? 'failed' : job.stage === 'done' ? 'ready' : job.stage}
            </Badge>
          </div>

          <ol className="mt-5 space-y-3">
            {STAGES.map((stage, i) => {
              // A stage is only ticked if it actually finished. The stage that broke is
              // marked broken, and nothing after it can claim to be done.
              const broken = (i === 1 && transcribeFailed) || (i === 2 && extractFailed)
              const blockedByEarlier =
                (i >= 1 && transcribeFailed) || (i >= 2 && extractFailed)
              const reached = activeIndex >= i && !blockedByEarlier
              const current = activeIndex === i && job.stage !== 'done' && !broken
              return (
                <li key={stage.key} className="flex items-center gap-3">
                  <span
                    className={clsx(
                      'grid size-7 shrink-0 place-items-center rounded-lg',
                      broken
                        ? 'bg-danger/15 text-danger'
                        : reached
                          ? 'bg-accent-fill text-accent-on'
                          : 'bg-ink-faint/15 text-ink-faint',
                    )}
                  >
                    <i
                      className={clsx('bi text-xs', broken ? 'bi-x-lg' : current ? 'bi-arrow-repeat animate-spin' : stage.icon)}
                      aria-hidden
                    />
                  </span>
                  <span className={clsx('text-sm', reached ? 'text-ink' : 'text-ink-faint')}>{stage.label}</span>
                  {i === 1 && job.transcribeProvider && reached && (
                    <span className="mono-num ml-auto text-ink-faint">{job.transcribeProvider}</span>
                  )}
                  {i === 2 && job.extractProvider && reached && (
                    <span className="mono-num ml-auto text-ink-faint">{job.extractProvider}</span>
                  )}
                </li>
              )
            })}
          </ol>

          <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-ink/[0.08]">
            <div
              className={clsx('h-full rounded-full transition-all duration-500', failed ? 'bg-danger' : 'bg-accent-fill')}
              style={{ width: `${job.progress}%` }}
            />
          </div>

          {job.transcribeError && (
            <p className="mt-4 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2.5 text-xs text-danger">
              <i className="bi bi-exclamation-triangle-fill mr-1.5" aria-hidden />
              {job.transcribeError}
            </p>
          )}

          {job.extractError && (
            <div className="mt-4 rounded-xl border border-warn/45 bg-warn/[0.08] px-3 py-2.5">
              <p className="text-xs text-warn">
                <i className="bi bi-exclamation-triangle-fill mr-1.5" aria-hidden />
                The transcript is fine, but extraction failed: {job.extractError}
              </p>
              {/*
                * "A provider is configured now" is only encouraging when the missing
                * provider was the problem. For an unusable model the key is already
                * valid and retrying unchanged returns the same error, so that line would
                * send the user round a loop — it is replaced by what to actually change.
                */}
              {job.extractErrorCode === 'model_unavailable' ? (
                <p className="mt-1.5 text-xs text-ink-muted">
                  Retrying without changing anything will return the same error.
                  {job.extractModel && (
                    <>
                      {' '}
                      The configured model was <code className="font-mono">{job.extractModel}</code>.
                    </>
                  )}
                </p>
              ) : (
                status.extract.available && (
                  <p className="mt-1.5 text-xs text-ok">
                    <i className="bi bi-check-circle-fill mr-1.5" aria-hidden />
                    {status.extract.provider} is configured now — this will work if you retry.
                  </p>
                )
              )}
              <Button
                variant="secondary"
                icon="bi-arrow-clockwise"
                className="mt-2.5"
                onClick={async () => {
                  const response = await apiFetch(`/api/transcripts/${job.id}/extract`, { method: 'POST' })
                  const json = (await response.json()) as { ok?: boolean; created?: number }
                  if (json.ok) {
                    toast.push({ tone: 'success', title: `Extracted ${json.created} action items` })
                    const detail = await apiJson<TranscriptState>(`/api/transcripts/${job.id}`)
                    setJob(detail)
                    router.refresh()
                  } else {
                    toast.push({
                      tone: 'error',
                      title: 'Extraction failed again',
                      detail: apiMessage(json, 'The provider did not return a result.'),
                    })
                    // Refresh so the row picks up the new code and its guidance.
                    const refreshed = await apiJson<TranscriptState>(`/api/transcripts/${job.id}`)
                    setJob(refreshed)
                    router.refresh()
                  }
                }}
              >
                Retry extraction only
              </Button>
            </div>
          )}

          {extractionSucceeded && (
            <div className="mt-5 border-t border-edge/20 pt-4">
              {job.counts.actionItems === 0 && job.counts.decisions === 0 ? (
                /*
                 * Zero is a legitimate answer, and saying only "0 action items" next to a
                 * green tick reads as a malfunction. A four-second clip, or a
                 * conversation where nobody committed to anything, genuinely contains
                 * nothing to act on — and the extractor is told to prefer precision over
                 * recall, so an empty result is the correct one rather than a shortfall.
                 */
                <div>
                  <p className="text-sm">
                    <i className="bi bi-info-circle-fill mr-1.5 text-accent" aria-hidden />
                    Nothing to act on in this recording.
                  </p>
                  <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">
                    The transcript came through, but nobody committed to anything in it — no tasks,
                    deadlines or decisions. That is the expected result for a short clip or a
                    conversation that did not settle anything, and the extractor is deliberately
                    built to return nothing rather than invent something plausible.
                  </p>
                  {(job.durationMs ?? 0) < 20_000 && (
                    <p className="mt-1.5 text-xs text-ink-faint">
                      <i className="bi bi-stopwatch mr-1" aria-hidden />
                      This recording is {Math.round((job.durationMs ?? 0) / 1000)} seconds long. Try a
                      real meeting — even a minute of conversation usually contains something.
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-sm">
                  <i className="bi bi-check-circle-fill mr-1.5 text-ok" aria-hidden />
                  {job.counts.actionItems} action {job.counts.actionItems === 1 ? 'item' : 'items'} and{' '}
                  {job.counts.decisions} {job.counts.decisions === 1 ? 'decision' : 'decisions'} extracted.
                </p>
              )}
              {!job.diarized && (
                <p className="mt-1.5 text-[11px] text-ink-faint">
                  <i className="bi bi-info-circle mr-1" aria-hidden />
                  Speakers were not separated — Whisper does not diarize, so segments are unattributed.
                </p>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                <Link href={`/dashboard/transcripts/${job.id}`}>
                  <Button variant="accent" icon="bi-file-earmark-text">
                    Read the transcript
                  </Button>
                </Link>
                {job.counts.actionItems > 0 && (
                  <Link href={`/dashboard/action-items?transcriptId=${job.id}`}>
                    <Button variant="primary" icon="bi-list-check">
                      Review the action items
                    </Button>
                  </Link>
                )}
                <Button
                  variant="secondary"
                  icon="bi-plus-lg"
                  onClick={() => setJob(null)}
                >
                  Upload another
                </Button>
              </div>
            </div>
          )}
        </GlassCard>
      )}
    </div>
  )
}
