import type { ActionType } from '@/domain/types'
import { isProviderLive } from '@/lib/env'
import { googleCalendar } from './google-calendar'
import { gmail } from './gmail'
import { notion } from './notion'
import { sendgrid } from './sendgrid'
import { slack } from './slack'
import type { AuthKind, IntegrationMode, IntegrationProvider, ProviderId } from './types'

/**
 * Provider registry — SPEC-002 §3.1.
 *
 * Adding an integration means adding one entry here and one adapter file. No
 * change to `app/`, `server/`, or `domain/`; that is the extension point a
 * reviewer will probe, so it is deliberately the cheapest change in the codebase.
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- heterogeneous payload types by design
const REGISTRY: Record<ProviderId, IntegrationProvider<any, any>> = {
  google_calendar: googleCalendar,
  notion,
  gmail,
  sendgrid,
  slack,
}

/**
 * Default provider per capability, used when the user has no override.
 *
 * EMAIL has two adapters: Gmail drafts and sends *as the user*, SendGrid delivers
 * *as the organisation* with no per-user consent. Gmail is the default because it
 * can save a draft, which is the reversible option — the safe default matters more
 * than the convenient one here.
 */
const DEFAULT_BY_CAPABILITY: Partial<Record<ActionType, ProviderId>> = {
  CALENDAR: 'google_calendar',
  TASK: 'notion',
  EMAIL: 'gmail',
  REMINDER: 'slack',
}

/** Every provider that can serve a capability, default first. */
export function providersFor(actionType: ActionType): IntegrationProvider[] {
  const fallback = DEFAULT_BY_CAPABILITY[actionType]
  return allProviders()
    // Gated providers are excluded so the routing picker cannot offer a route that
    // resolution will then refuse. Mirrors `providers_for` in the Python registry.
    .filter((p) => p.capability === actionType && (p.status ?? 'live') === 'live')
    .sort((a, b) => (a.id === fallback ? -1 : b.id === fallback ? 1 : 0))
}

/** Capabilities with more than one adapter — the ones worth offering a choice for. */
export function routableCapabilities(): { capability: ActionType; providers: IntegrationProvider[] }[] {
  const types: ActionType[] = ['CALENDAR', 'TASK', 'EMAIL', 'REMINDER']
  return types
    .map((capability) => ({ capability, providers: providersFor(capability) }))
    .filter((entry) => entry.providers.length > 1)
}

export function allProviders(): IntegrationProvider[] {
  return Object.values(REGISTRY)
}

export function getProvider(id: string): IntegrationProvider | null {
  return (REGISTRY as Record<string, IntegrationProvider | undefined>)[id] ?? null
}

/**
 * Resolves an action type to a provider, honouring a per-user routing override.
 * Returns null rather than guessing — an unroutable action must surface as
 * `422 no_provider`, never as a silent no-op.
 */
export function resolveProvider(
  actionType: ActionType,
  routing: Partial<Record<ActionType, string>> = {},
): IntegrationProvider | null {
  const override = routing[actionType]
  if (override) {
    const provider = getProvider(override)
    // An override naming a provider that cannot serve this action type is a
    // misconfiguration; fall through to the default rather than mis-executing. A *gated*
    // override falls through too — a stored preference must not resurrect a withdrawn
    // provider.
    if (provider && provider.capability === actionType && (provider.status ?? 'live') === 'live') {
      return provider
    }
  }
  const fallback = DEFAULT_BY_CAPABILITY[actionType]
  const candidate = fallback ? (REGISTRY[fallback] ?? null) : null
  // Null rather than the next provider with this capability: falling through would send
  // via somewhere the reviewer never approved.
  return candidate && (candidate.status ?? 'live') === 'live' ? candidate : null
}

export function providerMode(id: ProviderId): IntegrationMode {
  return isProviderLive(id) ? 'live' : 'mock'
}

export interface ProviderStatus {
  id: ProviderId
  displayName: string
  capability: ActionType
  auth: AuthKind
  credentialService: string | null
  mode: IntegrationMode
  /** Whether the deployment-wide credentials exist. Live mode needs them. */
  configured: boolean
  /** 'planned' renders as coming-soon instead of a Connect button. */
  status: 'live' | 'planned'
}

export function providerStatuses(): ProviderStatus[] {
  return allProviders().map((p) => ({
    id: p.id,
    displayName: p.displayName,
    capability: p.capability,
    auth: p.auth,
    credentialService: p.credentialService ?? null,
    mode: providerMode(p.id),
    configured: p.isConfigured(),
    status: p.status ?? 'live',
  }))
}
