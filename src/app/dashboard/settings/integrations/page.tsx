import Link from 'next/link'
import { Badge, GlassCard } from '@/components/ui/primitives'
import { currentUser } from '@/lib/auth'
import { db } from '@/lib/db'
import { env } from '@/lib/env'
import { providerStatuses } from '@/integrations/registry'

export const dynamic = 'force-dynamic'

/**
 * Integration connections — the OAuth half of the story (SPEC-002 §4).
 * Distinct from `/settings/credentials`, which holds plain API keys: this page is
 * about per-user consent grants, which are a different object with a different
 * lifecycle (they expire, they get revoked, they need reconnecting).
 */
export default async function IntegrationsPage({
  searchParams,
}: {
  searchParams: Promise<{ connected?: string; error?: string }>
}) {
  const { connected, error } = await searchParams
  const user = await currentUser()
  const accounts = await db.integrationAccount.findMany({
    where: { userId: user.id },
    select: {
      provider: true,
      accountLabel: true,
      needsReauth: true,
      connectedAt: true,
      expiresAt: true,
      scopes: true,
    },
  })
  const byProvider = new Map(accounts.map((a) => [a.provider, a]))
  const providers = providerStatuses()
  const mode = env().INTEGRATIONS_MODE

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Integrations</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Where approved action items are executed. Connecting an account grants this app
          permission to act on your behalf — tokens are encrypted at rest and never leave the server.
        </p>
      </header>

      {connected && (
        <GlassCard className="border-risk-low/40 p-3.5">
          <p className="text-sm text-risk-low">
            <i className="bi bi-check-circle-fill mr-2" aria-hidden />
            {connected} connected.
          </p>
        </GlassCard>
      )}
      {error && (
        <GlassCard className="border-risk-high/40 p-3.5">
          <p className="text-sm text-risk-high">
            <i className="bi bi-exclamation-triangle-fill mr-2" aria-hidden />
            {error}
          </p>
        </GlassCard>
      )}

      <GlassCard className={mode === 'mock' ? 'border-risk-low/30 p-4' : 'border-risk-medium/40 p-4'}>
        <p className="flex items-start gap-2.5 text-sm">
          <i
            className={`bi ${mode === 'mock' ? 'bi-shield-check text-risk-low' : 'bi-broadcast text-risk-medium'} mt-0.5`}
            aria-hidden
          />
          <span>
            <span className="font-medium">
              {mode === 'mock' ? 'Mock mode is active.' : 'Live mode is active.'}
            </span>{' '}
            <span className="text-ink-muted">
              {mode === 'mock'
                ? 'Executions are simulated end to end and produce fake ids, so the whole approval path is testable with no credentials. Set INTEGRATIONS_MODE=live to reach real accounts.'
                : 'Executions reach real third-party accounts. Every one is recorded in the audit log.'}
            </span>
          </span>
        </p>
      </GlassCard>

      <div className="grid gap-3 sm:grid-cols-2">
        {providers.map((provider) => {
          const account = byProvider.get(provider.id)
          const connectedOk = Boolean(account) && !account?.needsReauth

          return (
            <GlassCard key={provider.id} className="flex flex-col p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold">{provider.displayName}</h2>
                  <p className="mt-0.5 text-xs text-ink-faint">
                    Executes <span className="text-ink-muted">{provider.capability}</span> actions
                  </p>
                </div>
                <Badge
                  tone={connectedOk ? 'success' : account?.needsReauth ? 'danger' : 'muted'}
                  icon={connectedOk ? 'bi-plug-fill' : 'bi-plug'}
                >
                  {connectedOk ? 'connected' : account?.needsReauth ? 'needs reauth' : 'not connected'}
                </Badge>
              </div>

              <dl className="mt-3 space-y-1 text-[11px] text-ink-faint">
                <div className="flex gap-2">
                  <dt>Mode</dt>
                  <dd className={provider.mode === 'live' ? 'text-risk-medium' : 'text-risk-low'}>
                    {provider.mode}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt>OAuth app</dt>
                  <dd className={provider.configured ? 'text-risk-low' : 'text-risk-medium'}>
                    {provider.configured ? 'configured' : 'missing client id / secret'}
                  </dd>
                </div>
                {account && (
                  <div className="flex gap-2">
                    <dt>Account</dt>
                    <dd className="text-ink-muted">{account.accountLabel ?? '—'}</dd>
                  </div>
                )}
              </dl>

              <div className="mt-4 flex items-center gap-2 border-t border-edge pt-3">
                {provider.configured ? (
                  <a
                    href={`/api/integrations/${provider.id}/authorize`}
                    className="inline-flex items-center gap-2 rounded-xl border border-accent/50 bg-accent/20 px-3 py-1.5 text-sm font-medium text-accent hover:bg-accent/30"
                  >
                    <i className="bi bi-box-arrow-in-right" aria-hidden />
                    {account ? 'Reconnect' : 'Connect'}
                  </a>
                ) : (
                  <Link
                    href="/dashboard/settings/credentials"
                    className="inline-flex items-center gap-2 rounded-xl border border-edge bg-surface-strong px-3 py-1.5 text-sm font-medium hover:bg-white/10"
                  >
                    <i className="bi bi-key" aria-hidden />
                    Add its client id and secret
                  </Link>
                )}
                {provider.mode === 'mock' && (
                  <span className="text-[11px] text-ink-faint">
                    Works without connecting while in mock mode.
                  </span>
                )}
              </div>
            </GlassCard>
          )
        })}
      </div>
    </div>
  )
}
