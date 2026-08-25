import Link from 'next/link'
import { Badge, Button, EmptyState, GlassCard } from '@/components/ui/primitives'
import { BrdExportLink } from '@/components/brd/BrdExportLink'
import { DeleteBrdDocument } from '@/components/brd/DeleteBrdDocument'
import { apiServerJson } from '@/lib/api-server'
import type { BrdSummary } from '@/server/brd/service'

export const dynamic = 'force-dynamic'

/** `/dashboard/brd` — the requirements-document history (SPEC-014 §8). */
export default async function BrdHistoryPage() {
  const { documents } = await apiServerJson<{ documents: BrdSummary[] }>('/api/brd')

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Requirements documents</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">
            Everything you have dictated. Open one to read it, see what each spoken turn changed, or
            export it.
          </p>
        </div>
        <Link href="/dashboard">
          <Button variant="primary" icon="bi-mic-fill">
            New document
          </Button>
        </Link>
      </header>

      {documents.length === 0 ? (
        <GlassCard className="p-10">
          <EmptyState icon="bi-file-earmark-text" title="No documents yet">
            Press the record button on the dashboard and describe what you need built.
            <div className="mt-4">
              <Link href="/dashboard">
                <Button variant="accent" icon="bi-mic-fill">
                  Start dictating
                </Button>
              </Link>
            </div>
          </EmptyState>
        </GlassCard>
      ) : (
        <div className="space-y-3">
          {documents.map((d) => (
            <GlassCard key={d.id} className="p-4 sm:p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/dashboard/brd/${d.id}`}
                      className="text-sm font-medium text-ink hover:text-accent"
                    >
                      {d.title}
                    </Link>
                    {d.status === 'FAILED' && <Badge tone="danger">Generation failed</Badge>}
                    {d.status === 'DRAFTING' && <Badge tone="warn">Interrupted</Badge>}
                  </div>

                  <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
                    <span>
                      <i className="bi bi-list-check mr-1.5" aria-hidden />
                      {d.requirementCount} requirements
                    </span>
                    <span>
                      <i className="bi bi-patch-question mr-1.5" aria-hidden />
                      {d.openQuestionCount} open questions
                    </span>
                    <span>
                      <i className="bi bi-clock-history mr-1.5" aria-hidden />
                      {d.revisionCount} revision{d.revisionCount === 1 ? '' : 's'}
                    </span>
                    <span>
                      <i className="bi bi-calendar3 mr-1.5" aria-hidden />
                      {new Date(d.updatedAt).toLocaleDateString(undefined, {
                        day: 'numeric',
                        month: 'short',
                        year: 'numeric',
                      })}
                    </span>
                  </div>

                  {d.lastError && (
                    <p className="mt-2.5 rounded-lg border border-warn/40 bg-warn/[0.07] px-2.5 py-1.5 text-xs text-warn">
                      {d.lastError}
                    </p>
                  )}
                  {d.status === 'DRAFTING' && !d.lastError && (
                    <p className="mt-2.5 text-xs leading-relaxed text-ink-muted">
                      This recording was interrupted before a document was written. What you said is
                      kept — open it to see the transcript.
                    </p>
                  )}
                </div>

                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  <Link href={`/dashboard/brd/${d.id}`}>
                    <Button variant="secondary" icon="bi-file-earmark-text">
                      Open
                    </Button>
                  </Link>
                  {d.requirementCount > 0 && (
                    <BrdExportLink documentId={d.id} format="md" label="Markdown" icon="bi-download" />
                  )}
                  <DeleteBrdDocument
                    documentId={d.id}
                    title={d.title}
                    revisionCount={d.revisionCount}
                  />
                </div>
              </div>
            </GlassCard>
          ))}
        </div>
      )}
    </div>
  )
}
