import Link from 'next/link'
import { notFound } from 'next/navigation'
import { Badge, Button, GlassCard } from '@/components/ui/primitives'
import { findModule } from '@/lib/navigation'

/*
 * Dynamic rather than prerendered: the content is static data, but the shared
 * dashboard layout resolves the signed-in user, and `currentUser()` deliberately
 * fails closed at build time when no auth provider is configured. Prerendering
 * would mean either weakening that guard or rendering a page with no account
 * context — neither is worth the milliseconds.
 */
export const dynamic = 'force-dynamic'

/**
 * `/dashboard/modules/[slug]` — the destination for modules that are specified
 * but not yet built (SPEC-005 §2).
 *
 * The alternative designs were worse: hiding unfinished modules makes the tool
 * look smaller than it is, and linking them to a 404 makes it look broken. A page
 * that says what the feature does, which spec defines it, and what already exists
 * in the schema is honest and still useful.
 */
export default async function ModulePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  const found = findModule(slug)
  if (!found) notFound()

  const { module, group } = found
  const siblings = group.modules.filter((m) => m.slug !== module.slug)

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <nav className="flex items-center gap-2 text-xs text-ink-faint" aria-label="Breadcrumb">
        <Link href="/dashboard" className="hover:text-ink">
          Dashboard
        </Link>
        <i className="bi bi-chevron-right text-[9px]" aria-hidden />
        <span>{group.label}</span>
        <i className="bi bi-chevron-right text-[9px]" aria-hidden />
        <span className="text-ink-muted">{module.label}</span>
      </nav>

      <header className="flex items-start gap-4">
        <span
          className={
            module.status === 'live'
              ? 'grid size-12 shrink-0 place-items-center rounded-2xl bg-accent-fill text-accent-on'
              : 'grid size-12 shrink-0 place-items-center rounded-2xl bg-ink-faint/15 text-ink-faint'
          }
        >
          <i className={`bi ${module.icon} text-xl`} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{module.label}</h1>
            <Badge tone={module.status === 'live' ? 'success' : 'muted'}>
              {module.status === 'live' ? 'available' : 'specified, not yet built'}
            </Badge>
          </div>
          <p className="mt-1.5 text-sm text-ink-muted">{module.blurb}</p>
        </div>
      </header>

      <GlassCard className="lit-edge p-5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">How it works</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-muted">{module.detail}</p>

        <h3 className="mt-5 text-sm font-semibold uppercase tracking-wide text-ink-muted">
          What it covers
        </h3>
        <ul className="mt-2 space-y-2">
          {module.highlights.map((highlight) => (
            <li key={highlight} className="flex gap-2.5 text-sm text-ink-muted">
              <i
                className={
                  module.status === 'live'
                    ? 'bi bi-check2-circle mt-0.5 shrink-0 text-ok'
                    : 'bi bi-circle mt-0.5 shrink-0 text-ink-faint'
                }
                aria-hidden
              />
              <span>{highlight}</span>
            </li>
          ))}
        </ul>

        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-edge/20 pt-4">
          <span className="mono-num rounded border border-edge/25 px-2 py-1 text-ink-faint">
            {module.spec}
          </span>
          <span className="text-[11px] text-ink-faint">
            {module.status === 'live'
              ? 'Implemented and covered by the smoke suite.'
              : 'The specification is written and the schema is in place; the surface is not built yet.'}
          </span>
        </div>
      </GlassCard>

      {module.status === 'planned' && (
        <GlassCard className="p-5">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
            <i className="bi bi-signpost-split mr-2 text-accent" aria-hidden />
            What you can do today
          </h2>
          <p className="mt-2 text-sm text-ink-muted">
            The execution half of the product is complete. Seeded meetings already contain action items,
            so you can walk the full review-and-execute path right now.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/dashboard/action-items">
              <Button variant="primary" icon="bi-list-check">
                Open action items
              </Button>
            </Link>
            <Link href="/dashboard/settings/credentials">
              <Button variant="secondary" icon="bi-key">
                Add API keys
              </Button>
            </Link>
          </div>
        </GlassCard>
      )}

      {siblings.length > 0 && (
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            Also in {group.label}
          </h2>
          <div className="grid gap-2 sm:grid-cols-2">
            {siblings.map((sibling) => (
              <Link
                key={sibling.slug}
                href={sibling.href}
                className="panel flex items-center gap-3 rounded-xl p-3 transition-colors hover:border-accent-fill/50"
              >
                <i className={`bi ${sibling.icon} shrink-0 text-ink-faint`} aria-hidden />
                <span className="min-w-0 flex-1 truncate text-sm">{sibling.label}</span>
                {sibling.status === 'planned' && (
                  <span className="shrink-0 rounded border border-edge/25 px-1 text-[9px] uppercase tracking-wide text-ink-faint">
                    soon
                  </span>
                )}
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
