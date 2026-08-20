import clsx from 'clsx'
import Link from 'next/link'
import { Badge, GlassCard } from '@/components/ui/primitives'
import { NAV_GROUPS } from '@/lib/navigation'
import type { OverviewData } from '@/server/analytics/service'

/**
 * Landing overview — SPEC-005 §7.
 *
 * Ordered by the question a reviewer actually arrives with: *is there anything
 * waiting for me?* The pending-decision count is therefore the largest thing on
 * the page, and everything else supports it. A dashboard that leads with totals
 * looks impressive and tells you nothing about what to do next.
 *
 * A server component: every number is a database aggregate, so there is nothing
 * for the client to fetch or hold.
 */

const fmt = new Intl.NumberFormat('en-US')

function duration(ms: number): string {
  if (ms <= 0) return '—'
  const hours = Math.floor(ms / 3_600_000)
  const minutes = Math.round((ms % 3_600_000) / 60_000)
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`
}

export function Overview({ data, userName }: { data: OverviewData; userName: string | null }) {
  const { headline, funnel, rates, guardrails, usage, transcripts, system } = data
  const needsAttention = headline.pendingDecisions + headline.failedNeedingAttention

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
            {userName ? `Welcome back, ${userName.split(' ')[0]}` : 'Overview'}
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            {needsAttention === 0
              ? 'Nothing is waiting on you right now.'
              : `${needsAttention} ${needsAttention === 1 ? 'item needs' : 'items need'} your attention.`}
          </p>
        </div>
        <Badge tone={system.integrationsMode === 'mock' ? 'success' : 'warn'} icon="bi-shield-check">
          {system.integrationsMode} mode
        </Badge>
      </header>

      {/* ── 1. where does work stand */}
      <section aria-labelledby="ov-standing" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <h2 id="ov-standing" className="sr-only">
          Where work stands
        </h2>
        <Stat
          label="Awaiting your decision"
          value={headline.pendingDecisions}
          icon="bi-hourglass-split"
          tone={headline.pendingDecisions > 0 ? 'accent' : 'muted'}
          href="/dashboard/action-items?status=PROPOSED"
          hint="Proposed, not yet approved or rejected"
          emphasis
        />
        <Stat
          label="Approved, ready to run"
          value={headline.readyToExecute}
          icon="bi-lightning-charge-fill"
          tone={headline.readyToExecute > 0 ? 'success' : 'muted'}
          href="/dashboard/action-items?status=APPROVED"
          hint="Cleared the guardrails and your review"
        />
        <Stat
          label="Executed"
          value={headline.executedAllTime}
          icon="bi-check-circle-fill"
          tone="success"
          href="/dashboard/action-items?status=EXECUTED"
          hint="A real side effect exists for each"
        />
        <Stat
          label="Failed"
          value={headline.failedNeedingAttention}
          icon="bi-x-octagon-fill"
          tone={headline.failedNeedingAttention > 0 ? 'danger' : 'muted'}
          href="/dashboard/action-items?status=FAILED"
          hint="Retryable — the error is on the card"
        />
      </section>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ── 2. is the workflow moving */}
        <GlassCard className="p-5 lg:col-span-2">
          <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
              <i className="bi bi-funnel mr-2 text-accent" aria-hidden />
              Workflow
            </h2>
            <p className="text-xs text-ink-faint">Extraction through to a real-world effect</p>
          </div>

          <ol className="space-y-3">
            {funnel.map((stage, i) => {
              const widest = funnel[0]?.count ?? 1
              const width = widest === 0 ? 0 : Math.max(2, Math.round((stage.count / widest) * 100))
              return (
                <li key={stage.key}>
                  <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                    <span className="font-medium">{stage.label}</span>
                    <span className="flex items-baseline gap-2">
                      <span className="mono-num text-ink-muted">{fmt.format(stage.count)}</span>
                      {stage.ofPrevious !== null && (
                        <span
                          className={clsx(
                            'mono-num',
                            stage.ofPrevious >= 66
                              ? 'text-ok'
                              : stage.ofPrevious >= 33
                                ? 'text-warn'
                                : 'text-danger',
                          )}
                          title={`${stage.ofPrevious}% of ${funnel[i - 1]?.label ?? 'the previous stage'}`}
                        >
                          {stage.ofPrevious}%
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-ink/[0.07]">
                    <div
                      className={clsx('h-full rounded-full', i === funnel.length - 1 ? 'bg-ok' : 'bg-accent-fill')}
                      style={{ width: `${width}%` }}
                    />
                  </div>
                  <p className="mt-1 text-[11px] text-ink-faint">{stage.hint}</p>
                </li>
              )
            })}
          </ol>

          <dl className="mt-5 grid grid-cols-2 gap-3 border-t border-edge/20 pt-4 sm:grid-cols-4">
            <Rate label="Decision rate" value={`${rates.decisionRate}%`} />
            <Rate label="Approval rate" value={`${rates.approvalRate}%`} />
            <Rate label="Execution success" value={`${rates.executionSuccessRate}%`} />
            <Rate
              label="Avg execution"
              value={rates.avgExecutionMs === null ? '—' : `${rates.avgExecutionMs} ms`}
            />
          </dl>
        </GlassCard>

        {/* ── 3. what did the guardrails do */}
        <GlassCard className="p-5">
          <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-ink-muted">
            <i className="bi bi-shield-exclamation mr-2 text-accent" aria-hidden />
            Guardrails
          </h2>
          <p className="text-xs text-ink-faint">
            A guardrail nobody can see is a guardrail nobody trusts.
          </p>

          <p className="mt-4 flex items-baseline gap-2">
            <span className="text-3xl font-semibold tabular-nums">{fmt.format(guardrails.blockedExecutions)}</span>
            <span className="text-xs text-ink-muted">executions blocked</span>
          </p>

          {guardrails.topRules.length > 0 ? (
            <ul className="mt-4 space-y-1.5">
              {guardrails.topRules.map((rule) => (
                <li key={`${rule.ruleId}:${rule.severity}`} className="flex items-center gap-2 text-xs">
                  <Badge tone={rule.severity === 'BLOCK' ? 'danger' : rule.severity === 'WARN' ? 'warn' : 'muted'}>
                    {rule.severity}
                  </Badge>
                  <span className="mono-num flex-1 truncate text-ink-muted">{rule.ruleId}</span>
                  <span className="mono-num text-ink-faint">{rule.count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-4 text-xs text-ink-faint">
              No rule has fired yet. They are evaluated on every execution attempt.
            </p>
          )}

          <Link
            href="/dashboard/audit-log?event=action_item.guardrail_blocked"
            className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:underline"
          >
            See them in the audit log <i className="bi bi-arrow-right text-[10px]" aria-hidden />
          </Link>
        </GlassCard>
      </div>

      {/* ── 4. usage and system state */}
      <div className="grid gap-6 lg:grid-cols-3">
        <GlassCard className="p-5 lg:col-span-2">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-ink-muted">
            <i className="bi bi-graph-up mr-2 text-accent" aria-hidden />
            Usage
          </h2>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Rate label="Meetings processed" value={fmt.format(transcripts.total)} />
            <Rate label="Audio transcribed" value={duration(transcripts.totalDurationMs)} />
            <Rate label="Languages seen" value={transcripts.languages.length || '—'} />
            <Rate label="Execution attempts" value={fmt.format(usage.executionAttempts)} />
            <Rate label="Simulated runs" value={fmt.format(usage.simulatedExecutions)} />
            <Rate label="Corrections captured" value={fmt.format(usage.correctionsCaptured)} />
            <Rate label="Audit events" value={fmt.format(usage.auditEvents)} />
            <Rate label="Accounts connected" value={`${usage.integrationsConnected}/${usage.providersRegistered}`} />
            <Rate
              label="Action items"
              value={fmt.format(Object.values(data.byStatus).reduce((a, b) => a + b, 0))}
            />
          </dl>

          {transcripts.recent.length > 0 && (
            <div className="mt-5 border-t border-edge/20 pt-4">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                Recent meetings
              </h3>
              <ul className="space-y-1">
                {transcripts.recent.map((t) => (
                  <li key={t.id}>
                    <Link
                      href={`/dashboard/action-items?transcriptId=${t.id}`}
                      className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-sm transition-colors hover:bg-ink/5"
                    >
                      <i className="bi bi-soundwave shrink-0 text-ink-faint" aria-hidden />
                      <span className="min-w-0 flex-1 truncate">{t.title}</span>
                      <span className="mono-num shrink-0 text-ink-faint">{t.actionItems} actions</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </GlassCard>

        <GlassCard className="p-5">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-muted">
            <i className="bi bi-cpu mr-2 text-accent" aria-hidden />
            Modules
          </h2>
          <ul className="space-y-2">
            {system.modules.map((module) => (
              <li key={module.module} className="flex items-center gap-2.5">
                <i className={clsx('bi', module.icon, 'shrink-0 text-ink-faint')} aria-hidden />
                <span className="min-w-0 flex-1 truncate text-sm">{module.title}</span>
                <Badge
                  tone={module.state === 'live' ? 'success' : module.state === 'mocked' ? 'warn' : 'muted'}
                >
                  {module.state}
                </Badge>
              </li>
            ))}
          </ul>
          <Link
            href="/dashboard/settings/credentials"
            className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:underline"
          >
            Add your own API keys <i className="bi bi-arrow-right text-[10px]" aria-hidden />
          </Link>

          {data.activity.length > 0 && (
            <div className="mt-5 border-t border-edge/20 pt-4">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                Latest activity
              </h3>
              <ul className="space-y-1.5">
                {data.activity.slice(0, 6).map((entry, i) => (
                  <li key={`${entry.at}-${i}`} className="text-[11px] leading-snug">
                    <span className="mono-num text-accent">{entry.event.split('.').pop()}</span>
                    {entry.description && <span className="text-ink-muted"> · {entry.description}</span>}
                  </li>
                ))}
              </ul>
              <Link
                href="/dashboard/audit-log"
                className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:underline"
              >
                Full audit log <i className="bi bi-arrow-right text-[10px]" aria-hidden />
              </Link>
            </div>
          )}
        </GlassCard>
      </div>

      {/* ── explore every module */}
      <section aria-labelledby="ov-explore">
        <div className="mb-3 flex items-baseline gap-3">
          <h2 id="ov-explore" className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
            <i className="bi bi-compass mr-2 text-accent" aria-hidden />
            Explore the tool
          </h2>
          <p className="text-xs text-ink-faint">
            Every module, including the ones specified but not yet built.
          </p>
        </div>

        <div className="space-y-5">
          {NAV_GROUPS.filter((g) => g.id !== 'overview').map((group) => (
            <div key={group.id}>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint">
                <i className={clsx('bi', group.icon, 'mr-1.5')} aria-hidden />
                {group.label}
              </h3>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {group.modules.map((module) => (
                  <Link
                    key={module.slug}
                    href={module.href}
                    className={clsx(
                      'panel group rounded-2xl p-4 transition-all hover:border-accent-fill/50',
                      module.status === 'planned' && 'opacity-80',
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <span
                        className={clsx(
                          'grid size-9 shrink-0 place-items-center rounded-xl',
                          module.status === 'live'
                            ? 'bg-accent-fill text-accent-on'
                            : 'bg-ink-faint/15 text-ink-faint',
                        )}
                      >
                        <i className={clsx('bi', module.icon)} aria-hidden />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="flex items-center gap-2 text-sm font-semibold">
                          {module.label}
                          {module.status === 'planned' && (
                            <span className="rounded border border-edge/30 px-1 text-[9px] font-medium uppercase tracking-wide text-ink-faint">
                              soon
                            </span>
                          )}
                        </p>
                        <p className="mt-1 text-xs leading-relaxed text-ink-muted">{module.blurb}</p>
                        <p className="mono-num mt-1.5 text-ink-faint">{module.spec}</p>
                      </div>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

function Stat({
  label,
  value,
  icon,
  tone,
  href,
  hint,
  emphasis,
}: {
  label: string
  value: number
  icon: string
  tone: 'accent' | 'success' | 'danger' | 'muted'
  href: string
  hint: string
  emphasis?: boolean
}) {
  const TONE = {
    accent: 'text-accent',
    success: 'text-ok',
    danger: 'text-danger',
    muted: 'text-ink-faint',
  } as const

  return (
    <Link
      href={href}
      className={clsx(
        'panel rounded-2xl p-4 transition-all hover:border-accent-fill/50',
        emphasis && value > 0 && 'lit-edge',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-ink-muted">{label}</p>
        <i className={clsx('bi', icon, TONE[tone])} aria-hidden />
      </div>
      <p className={clsx('mt-2 tabular-nums', emphasis ? 'text-4xl font-semibold' : 'text-3xl font-semibold')}>
        {fmt.format(value)}
      </p>
      <p className="mt-1 text-[11px] text-ink-faint">{hint}</p>
    </Link>
  )
}

function Rate({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-[11px] text-ink-faint">{label}</dt>
      <dd className="mt-0.5 text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  )
}
