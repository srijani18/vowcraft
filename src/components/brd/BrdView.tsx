import { Badge, GlassCard } from '@/components/ui/primitives'
import type { Brd } from '@/server/brd/schema'

/**
 * Renders a BRD — SPEC-014 §5.
 *
 * A server component: it is pure presentation over already-fetched data, so it ships no
 * JavaScript. Sections with no content are omitted rather than shown as empty headings,
 * with the deliberate exception of open questions (see below).
 */

const PRIORITY_TONE = {
  MUST: 'danger',
  SHOULD: 'warn',
  COULD: 'muted',
} as const

function Section({ title, icon, children }: { title: string; icon: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2.5">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <i className={`bi ${icon} text-accent`} aria-hidden />
        {title}
      </h3>
      {children}
    </section>
  )
}

const Bullets = ({ items }: { items: readonly string[] }) => (
  <ul className="space-y-1.5 text-sm leading-relaxed text-ink-muted">
    {items.map((item, i) => (
      <li key={i} className="flex gap-2.5">
        <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-accent/60" aria-hidden />
        <span>{item}</span>
      </li>
    ))}
  </ul>
)

export function BrdView({ document }: { document: Brd }) {
  const {
    executiveSummary,
    objectives,
    scope,
    stakeholders,
    functionalRequirements,
    nonFunctionalRequirements,
    assumptions,
    risks,
    openQuestions,
  } = document

  return (
    <div className="space-y-7">
      <Section title="Executive summary" icon="bi-file-text">
        <p className="text-sm leading-relaxed text-ink-muted">{executiveSummary}</p>
      </Section>

      {objectives.length > 0 && (
        <Section title="Objectives" icon="bi-bullseye">
          <Bullets items={objectives} />
        </Section>
      )}

      {(scope.inScope.length > 0 || scope.outOfScope.length > 0) && (
        <Section title="Scope" icon="bi-bounding-box">
          <div className="grid gap-4 sm:grid-cols-2">
            {scope.inScope.length > 0 && (
              <div>
                <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ok">In scope</p>
                <Bullets items={scope.inScope} />
              </div>
            )}
            {scope.outOfScope.length > 0 && (
              <div>
                <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-ink-faint">
                  Out of scope
                </p>
                <Bullets items={scope.outOfScope} />
              </div>
            )}
          </div>
        </Section>
      )}

      {functionalRequirements.length > 0 && (
        <Section title={`Functional requirements (${functionalRequirements.length})`} icon="bi-list-check">
          <div className="space-y-2">
            {functionalRequirements.map((r) => (
              <div
                key={r.id}
                className="rounded-xl border border-edge/20 bg-ink/[0.02] px-3 py-2.5 dark:bg-ink/[0.03]"
              >
                <div className="flex flex-wrap items-start gap-2">
                  {/* The id is monospace and first: it is a stable reference across
                      revisions (SPEC-014 §6.1), so it must be quotable at a glance. */}
                  <code className="mono-num shrink-0 rounded-md bg-mid/15 px-1.5 py-0.5 text-xs font-semibold text-accent">
                    {r.id}
                  </code>
                  <p className="min-w-0 flex-1 text-sm leading-relaxed text-ink">{r.requirement}</p>
                  <Badge tone={PRIORITY_TONE[r.priority]}>{r.priority}</Badge>
                </div>
                {r.rationale && (
                  <p className="mt-1.5 pl-1 text-xs leading-relaxed text-ink-faint">{r.rationale}</p>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {nonFunctionalRequirements.length > 0 && (
        <Section title="Non-functional requirements" icon="bi-sliders">
          <div className="space-y-2">
            {nonFunctionalRequirements.map((r) => (
              <div key={r.id} className="flex flex-wrap items-start gap-2">
                <code className="mono-num shrink-0 rounded-md bg-mid/15 px-1.5 py-0.5 text-xs font-semibold text-accent">
                  {r.id}
                </code>
                <Badge tone="neutral">{r.category}</Badge>
                <p className="min-w-0 flex-1 text-sm leading-relaxed text-ink-muted">{r.requirement}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {stakeholders.length > 0 && (
        <Section title="Stakeholders" icon="bi-people">
          <div className="space-y-1.5">
            {stakeholders.map((s, i) => (
              <div key={i} className="flex flex-wrap gap-x-2 text-sm">
                <span className="font-medium text-ink">{s.role}</span>
                <span className="text-ink-muted">— {s.interest}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {assumptions.length > 0 && (
        <Section title="Assumptions" icon="bi-question-square">
          <Bullets items={assumptions} />
        </Section>
      )}

      {risks.length > 0 && (
        <Section title="Risks" icon="bi-exclamation-triangle">
          <div className="space-y-1.5 text-sm leading-relaxed">
            {risks.map((r, i) => (
              <div key={i} className="flex gap-2.5">
                <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-warn/70" aria-hidden />
                <span>
                  <span className="text-ink">{r.risk}</span>
                  {r.mitigation && <span className="text-ink-muted"> — {r.mitigation}</span>}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/*
        * Rendered even when empty, unlike every other section — SPEC-014 §5.1.
        *
        * An empty list here is a *claim* that nothing further needs asking, and a reader
        * deciding whether to build from this document needs to see the claim made rather
        * than infer it from a missing heading. This is also the section that makes the
        * feature honest: a short spoken requirement cannot specify a system, and this is
        * where the model is required to say what it does not know.
        */}
      <GlassCard className="border-accent/25 bg-accent/[0.04] p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
          <i className="bi bi-patch-question text-accent" aria-hidden />
          Open questions
          {openQuestions.length > 0 && <Badge tone="accent">{openQuestions.length}</Badge>}
        </h3>
        {openQuestions.length > 0 ? (
          <>
            <p className="mt-1.5 text-xs text-ink-faint">
              Answer these by speaking again — the document updates in place rather than being
              rewritten.
            </p>
            <div className="mt-2.5">
              <Bullets items={openQuestions} />
            </div>
          </>
        ) : (
          <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">
            None recorded. Treat that as a claim to verify rather than a guarantee of completeness.
          </p>
        )}
      </GlassCard>
    </div>
  )
}
