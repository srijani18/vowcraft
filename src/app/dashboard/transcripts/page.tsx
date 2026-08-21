import Link from 'next/link'
import { Badge, Button, EmptyState, GlassCard } from '@/components/ui/primitives'
import { RetryExtraction } from '@/components/transcripts/RetryExtraction'
import { Uploader, type PipelineStatus } from '@/components/transcripts/Uploader'
import { apiServerJson } from '@/lib/api-server'
import type { TranscriptSummary } from '@/server/ingest/service'
import { formatTimestamp } from '@/lib/time'

export const dynamic = 'force-dynamic'

/** `/dashboard/transcripts` — upload and the transcript library (SPEC-010 §9). */
export default async function TranscriptsPage() {
  const [status, { transcripts }] = await Promise.all([
    apiServerJson<PipelineStatus>('/api/transcripts/pipeline-status'),
    apiServerJson<{ transcripts: TranscriptSummary[] }>('/api/transcripts'),
  ])

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Recordings</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Upload a meeting and it is transcribed with word-level timings, then read for what the
          conversation actually committed to. Everything it finds lands on the action items board,
          where nothing executes until you approve it.
        </p>
      </header>

      <Uploader initialStatus={status} />

      <Link
        href="/dashboard/transcripts/live"
        className="flex items-center gap-2 text-sm text-ink-muted underline decoration-dotted hover:text-ink"
      >
        <i className="bi bi-camera-video" aria-hidden />
        Or capture a live meeting by sharing a browser tab
      </Link>

      <section aria-labelledby="library">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="library" className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
            <i className="bi bi-collection mr-2 text-accent" aria-hidden />
            Library
          </h2>
          {transcripts.length > 0 && (
            <p className="text-xs text-ink-faint">
              {transcripts.length} {transcripts.length === 1 ? 'recording' : 'recordings'}
            </p>
          )}
        </div>

        {transcripts.length === 0 ? (
          <GlassCard className="p-6">
            <EmptyState icon="bi-soundwave" title="No recordings yet">
              Upload one above. If you have no API key configured, the bundled sample still runs the
              whole pipeline end to end so you can see what it produces.
            </EmptyState>
          </GlassCard>
        ) : (
          <div className="grid gap-3">
            {transcripts.map((t) => {
              const failed = t.status === 'FAILED' || Boolean(t.transcribeError)
              const working = t.stage !== 'done'
              // A transcript that extracted nothing is complete, not broken — but it is
              // also not "ready to review", and offering a review button that leads to an
              // empty board is worse than saying so.
              const emptyResult =
                !working && !failed && !t.extractError && t.counts.actionItems === 0
              return (
                <GlassCard key={t.id} className="p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                        <Badge
                          tone={failed ? 'danger' : working ? 'accent' : 'success'}
                          icon={failed ? 'bi-x-octagon' : working ? 'bi-arrow-repeat' : 'bi-check-circle'}
                        >
                          {failed ? 'failed' : working ? t.stage : 'ready'}
                        </Badge>
                        {t.language && <Badge tone="neutral" icon="bi-translate">{t.language.toUpperCase()}</Badge>}
                        {t.transcribeProvider === 'sample' && (
                          <Badge tone="warn" icon="bi-box">sample</Badge>
                        )}
                        {!t.diarized && !working && !failed && (
                          <Badge tone="muted" icon="bi-people" title="Whisper does not separate speakers">
                            speakers not separated
                          </Badge>
                        )}
                        {emptyResult && (
                          <Badge tone="neutral" icon="bi-info-circle" title="Nobody committed to anything in this recording">
                            nothing to act on
                          </Badge>
                        )}
                      </div>

                      <h3 className="text-[15px] font-semibold leading-snug">
                        {t.counts.segments > 0 ? (
                          <Link
                            href={`/dashboard/transcripts/${t.id}`}
                            className="transition-colors hover:text-accent hover:underline"
                          >
                            {t.title}
                          </Link>
                        ) : (
                          t.title
                        )}
                      </h3>

                      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
                        {t.durationMs && (
                          <span>
                            <i className="bi bi-stopwatch mr-1.5" aria-hidden />
                            <span className="mono-num">{formatTimestamp(t.durationMs)}</span>
                          </span>
                        )}
                        <span>
                          <i className="bi bi-list-check mr-1.5" aria-hidden />
                          {t.counts.actionItems} actions
                        </span>
                        <span>
                          <i className="bi bi-lightbulb mr-1.5" aria-hidden />
                          {t.counts.decisions} decisions
                        </span>
                        <span>
                          <i className="bi bi-calendar3 mr-1.5" aria-hidden />
                          {new Date(t.createdAt).toLocaleDateString(undefined, {
                            day: 'numeric',
                            month: 'short',
                            year: 'numeric',
                          })}
                        </span>
                      </div>

                      {t.summary && (
                        <p className="mt-2.5 text-xs leading-relaxed text-ink-muted">{t.summary}</p>
                      )}

                      {t.transcribeError && (
                        <p className="mt-2.5 rounded-lg border border-danger/30 bg-danger/[0.07] px-2.5 py-1.5 text-xs text-danger">
                          {t.transcribeError}
                        </p>
                      )}
                      {t.extractError && (
                        <div className="mt-2.5 rounded-lg border border-warn/40 bg-warn/[0.07] px-2.5 py-1.5">
                          <p className="text-xs text-warn">
                            Transcript is fine; extraction failed: {t.extractError}
                          </p>
                          {t.extractErrorCode === 'model_unavailable' ? (
                            /*
                             * The key is valid here — the model id is not. Offering
                             * "retry and this will work" would be false.
                             */
                            <p className="mt-1 text-xs text-ink-muted">
                              Retrying unchanged returns the same error.
                              {t.extractModel && (
                                <>
                                  {' '}
                                  The configured model was{' '}
                                  <code className="font-mono">{t.extractModel}</code>.
                                </>
                              )}
                            </p>
                          ) : (
                            status.extract.available && (
                              <p className="mt-1 text-xs text-ok">
                                {status.extract.provider} is configured now — re-extract and this will work.
                              </p>
                            )
                          )}
                        </div>
                      )}

                      {emptyResult && (
                        <p className="mt-2.5 text-xs leading-relaxed text-ink-muted">
                          Transcribed successfully, but nobody committed to anything in it — no tasks,
                          deadlines or decisions.
                          {t.durationMs !== null && t.durationMs < 20_000
                            ? ` At ${Math.round(t.durationMs / 1000)} seconds this is likely a test clip; a real meeting will produce more.`
                            : ' The extractor returns nothing rather than inventing something plausible.'}
                        </p>
                      )}
                    </div>

                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      {t.counts.segments > 0 && (
                        <Link href={`/dashboard/transcripts/${t.id}`}>
                          <Button variant="secondary" icon="bi-file-earmark-text">
                            Read
                          </Button>
                        </Link>
                      )}
                      {t.counts.actionItems > 0 && (
                        <Link href={`/dashboard/action-items?transcriptId=${t.id}`}>
                          <Button variant="accent" icon="bi-list-check">
                            {t.counts.actionItems} to review
                          </Button>
                        </Link>
                      )}
                      {/*
                        * Offered whenever extraction failed and there is text to extract
                        * from — including for an unusable model, where the fix is a
                        * config change and the retry is how the user confirms it worked.
                        */}
                      {t.extractError && t.counts.segments > 0 && <RetryExtraction transcriptId={t.id} />}
                    </div>
                  </div>
                </GlassCard>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
