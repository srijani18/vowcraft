import { notFound } from 'next/navigation'
import Link from 'next/link'
import { Badge, GlassCard } from '@/components/ui/primitives'
import { BrdView } from '@/components/brd/BrdView'
import { BrdExportLink } from '@/components/brd/BrdExportLink'
import { RefineRecorder } from '@/components/brd/RefineRecorder'
import { ApiServerError, apiServerJson } from '@/lib/api-server'
import type { BrdDetail } from '@/server/brd/service'

export const dynamic = 'force-dynamic'

/**
 * `/dashboard/brd/:id` — one document, its revisions, and the control to add to it
 * (SPEC-014 §6, §8).
 *
 * The revision list is the answer to "what did my last sentence actually do?". Each entry
 * pairs what was said with what changed, which is the only way refinement is auditable
 * without diffing two versions by eye.
 */
export default async function BrdDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params

  const document = await apiServerJson<BrdDetail>(`/api/brd/${id}`).catch((err) => {
    if (err instanceof ApiServerError && err.status === 404) return null
    throw err
  })
  if (!document) notFound()

  // Newest first: the most recent change is the one being asked about.
  const revisions = [...document.revisions].reverse()

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <Link href="/dashboard/brd" className="text-xs text-ink-muted hover:text-accent">
          <i className="bi bi-arrow-left mr-1.5" aria-hidden />
          All documents
        </Link>
      </div>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{document.title}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
            <span>
              {document.revisionCount} revision{document.revisionCount === 1 ? '' : 's'}
            </span>
            <span>·</span>
            <span>{document.requirementCount} requirements</span>
            {document.model && (
              <>
                <span>·</span>
                <span className="mono-num">{document.model}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {document.content && (
            <>
              <BrdExportLink documentId={document.id} format="md" label="Markdown" icon="bi-download" />
              <BrdExportLink documentId={document.id} format="json" label="JSON" icon="bi-filetype-json" />
            </>
          )}
        </div>
      </header>

      {document.lastError && (
        <p className="rounded-xl border border-warn/40 bg-warn/[0.07] px-3 py-2.5 text-xs text-warn">
          <i className="bi bi-exclamation-triangle-fill mr-1.5" aria-hidden />
          {document.lastError}
        </p>
      )}

      {/* Speak again to amend it in place — never a regeneration (SPEC-014 §6). */}
      <RefineRecorder documentId={document.id} hasContent={Boolean(document.content)} />

      {document.content ? (
        <GlassCard className="p-5 sm:p-6">
          <BrdView document={document.content} />
        </GlassCard>
      ) : (
        <GlassCard className="p-6">
          <div className="flex items-start gap-3">
            <i className="bi bi-mic-mute mt-0.5 text-lg text-warn" aria-hidden />
            <div>
              <h2 className="text-sm font-semibold text-ink">No document was written</h2>
              <p className="mt-1 text-sm leading-relaxed text-ink-muted">
                {document.status === 'DRAFTING'
                  ? 'This recording was interrupted before generation finished.'
                  : 'Generation failed.'}{' '}
                What you said is kept below — record again to try from it.
              </p>
            </div>
          </div>
        </GlassCard>
      )}

      {/* ── revision history ───────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-ink">
          <i className="bi bi-clock-history mr-2 text-accent" aria-hidden />
          What each turn changed
        </h2>
        {revisions.length === 0 ? (
          <p className="text-sm text-ink-muted">No revisions recorded.</p>
        ) : (
          revisions.map((r) => (
            <GlassCard key={r.id} className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={r.ordinal === 1 ? 'accent' : 'success'}>
                  {r.ordinal === 1 ? 'Initial' : `Revision ${r.ordinal}`}
                </Badge>
                <span className="text-xs text-ink-faint">
                  {new Date(r.createdAt).toLocaleString(undefined, {
                    day: 'numeric',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              </div>

              {r.changeSummary && (
                <p className="mt-2.5 text-sm leading-relaxed text-ink">{r.changeSummary}</p>
              )}

              {/* The spoken text for this turn alone — SPEC-014 §4.1. */}
              <details className="mt-2.5 group">
                <summary className="cursor-pointer list-none text-xs text-ink-muted hover:text-accent">
                  <i className="bi bi-chevron-right mr-1 inline-block transition-transform group-open:rotate-90" aria-hidden />
                  What you said
                </summary>
                <p className="mt-2 whitespace-pre-wrap rounded-lg border border-edge/20 bg-ink/[0.02] px-3 py-2 text-xs leading-relaxed text-ink-muted">
                  {r.spokenText}
                </p>
              </details>
            </GlassCard>
          ))
        )}
      </section>
    </div>
  )
}
