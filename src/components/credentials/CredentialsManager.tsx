'use client'

import clsx from 'clsx'
import { useMemo, useState } from 'react'
import { Badge, Button, Field, GlassCard, inputClass } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { apiFetch } from '@/lib/api-client'
import { MODULE_META, MODULE_ORDER, TIER_META } from '@/lib/credentials/catalog'
import type { CredentialModule, CredentialView, ModuleAvailability } from '@/lib/credentials/types'

/**
 * Bring-your-own-key dashboard — SPEC-004.
 *
 * Two rules shape this UI:
 *  1. There is no "reveal" affordance and no pre-filled input. Stored secrets
 *     have no read path, so an input shows a masked hint as placeholder and
 *     saving means writing a new value.
 *  2. Free tiers sort first and are labelled with their actual allowance, so the
 *     zero-cost path is the obvious one rather than the one you have to hunt for.
 */

const SOURCE_LABEL: Record<string, { label: string; tone: 'success' | 'accent' | 'muted' }> = {
  USER: { label: 'Your key', tone: 'success' },
  ENV: { label: 'From environment', tone: 'accent' },
  NONE: { label: 'Not configured', tone: 'muted' },
}

export function CredentialsManager({
  initial,
  initialModules,
  encryptionConfigured,
}: {
  initial: CredentialView[]
  initialModules: ModuleAvailability[]
  encryptionConfigured: boolean
}) {
  const toast = useToast()
  const [credentials, setCredentials] = useState(initial)
  const [modules, setModules] = useState(initialModules)
  const [openService, setOpenService] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [onlyFree, setOnlyFree] = useState(false)

  const byModule = useMemo(() => {
    const map = new Map<CredentialModule, CredentialView[]>()
    for (const module of MODULE_ORDER) {
      const list = credentials.filter((c) => c.module === module)
      // Free and local first — the catalogue already sorts, this preserves it
      // after the optional free-only filter.
      map.set(module, onlyFree ? list.filter((c) => c.tier === 'free' || c.tier === 'local') : list)
    }
    return map
  }, [credentials, onlyFree])

  const refresh = async () => {
    const response = await apiFetch('/api/credentials')
    if (!response.ok) return
    const json = (await response.json()) as { credentials: CredentialView[]; modules: ModuleAvailability[] }
    setCredentials(json.credentials)
    setModules(json.modules)
  }

  const save = async (service: string) => {
    const secrets = drafts[service] ?? {}
    if (Object.values(secrets).every((v) => !v?.trim())) {
      toast.push({ tone: 'error', title: 'Nothing to save', detail: 'Enter a key first.' })
      return
    }
    setBusy(service)
    try {
      const response = await apiFetch(`/api/credentials/${service}`, {
        method: 'PUT',
        body: JSON.stringify({ secrets }),
      })
      const json = (await response.json()) as { error?: { message: string } }
      if (!response.ok) throw new Error(json.error?.message ?? 'Save failed')

      // Clear the draft immediately: keeping a plaintext key in React state after
      // it is stored serves no purpose and widens the window for a leak.
      setDrafts((prev) => ({ ...prev, [service]: {} }))
      setOpenService(null)
      await refresh()
      toast.push({ tone: 'success', title: 'Key saved', detail: 'Encrypted at rest with AES-256-GCM.' })
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not save the key', detail: (err as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const verify = async (service: string) => {
    setBusy(service)
    try {
      const response = await apiFetch(`/api/credentials/${service}/verify`, { method: 'POST' })
      const json = (await response.json()) as CredentialView & { error?: { message: string } }
      if (!response.ok) throw new Error(json.error?.message ?? 'Verification failed')
      await refresh()
      toast.push({
        tone: json.status === 'VALID' ? 'success' : 'error',
        title: json.status === 'VALID' ? 'Key works' : 'Key rejected',
        detail: json.lastError ?? undefined,
      })
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not verify', detail: (err as Error).message })
    } finally {
      setBusy(null)
    }
  }

  /*
   * Disabling is how a user expresses *provider preference*. `resolve` walks the catalogue
   * in order and takes the first enabled entry, so turning one off promotes the next — the
   * only way to prefer a provider that ranks below a configured one. Deepgram is the case
   * that needs it: it is the only transcriber that separates speakers, but Groq is free and
   * therefore listed first, so "give me speaker labels" is expressed by switching Groq's
   * transcription key off rather than deleting it.
   */
  const setEnabled = async (service: string, enabled: boolean) => {
    setBusy(service)
    try {
      const response = await apiFetch(`/api/credentials/${service}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      })
      if (!response.ok) throw new Error(enabled ? 'Enable failed' : 'Disable failed')
      // A shared key serves more than one capability, so disabling it hands each dependent
      // its own copy first. Said out loud rather than done silently: a secret being
      // duplicated on the user's behalf is not something to discover later.
      const preserved = ((await response.json().catch(() => null)) as
        | { preservedFor?: string[] }
        | null)?.preservedFor
      await refresh()
      toast.push({
        tone: 'info',
        title: enabled ? 'Key enabled' : 'Key disabled',
        detail: enabled
          ? 'This provider is available again.'
          : preserved && preserved.length > 0
            ? `The key is kept, and ${preserved.join(' and ')} now holds its own copy so it keeps working.`
            : 'The key is kept, but this provider will be skipped.',
      })
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not change that', detail: (err as Error).message })
    } finally {
      setBusy(null)
    }
  }

  const remove = async (service: string) => {
    setBusy(service)
    try {
      const response = await apiFetch(`/api/credentials/${service}`, { method: 'DELETE' })
      if (!response.ok) throw new Error('Delete failed')
      await refresh()
      toast.push({ tone: 'info', title: 'Key removed' })
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not remove the key', detail: (err as Error).message })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">API keys</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">
            Bring your own keys for any module. They are encrypted at rest and never returned by the
            API — only a masked hint is. Anything left blank falls back to the deployment&apos;s
            environment, and modules with no key stay mocked rather than failing.
          </p>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={onlyFree}
            onChange={(e) => setOnlyFree(e.target.checked)}
            className="size-4 accent-ok"
          />
          Show free options only
        </label>
      </header>

      {!encryptionConfigured && (
        <GlassCard className="border-risk-high/40 p-4">
          <p className="flex items-start gap-2.5 text-sm text-risk-high">
            <i className="bi bi-shield-slash-fill mt-0.5" aria-hidden />
            <span>
              <span className="font-medium">APP_ENCRYPTION_KEY is not set,</span> so keys cannot be
              stored safely and saving is disabled. Generate one with{' '}
              <code className="rounded bg-abyss/[0.10] dark:bg-abyss/50 px-1 py-0.5 font-mono text-xs">openssl rand -base64 32</code>{' '}
              and restart.
            </span>
          </p>
        </GlassCard>
      )}

      {/* Availability banner — what actually works right now. */}
      <GlassCard className="p-4">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-ink-faint">
          Module status
        </h2>
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {modules.map((module) => (
            <div key={module.module} className="flex items-start gap-2.5 rounded-xl border border-edge bg-surface p-3">
              <i className={clsx('bi', module.icon, 'mt-0.5 text-accent')} aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 text-sm font-medium">{module.title}</p>
                <p className="mt-0.5 text-[11px] text-ink-faint">
                  {module.state === 'live' ? (
                    <>
                      Using <span className="text-ink-muted">{module.activeService}</span> ·{' '}
                      {SOURCE_LABEL[module.source]?.label}
                    </>
                  ) : module.state === 'mocked' ? (
                    'No key — running in mock mode.'
                  ) : (
                    'No key configured yet.'
                  )}
                </p>
              </div>
              <Badge
                tone={module.state === 'live' ? 'success' : module.state === 'mocked' ? 'warn' : 'muted'}
              >
                {module.state}
              </Badge>
            </div>
          ))}
        </div>
      </GlassCard>

      {MODULE_ORDER.map((module) => {
        const list = byModule.get(module) ?? []
        if (list.length === 0) return null
        const meta = MODULE_META[module]

        return (
          <section key={module} aria-labelledby={`cred-${module}`}>
            <div className="mb-3 flex items-baseline gap-2.5">
              <h2
                id={`cred-${module}`}
                className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-ink-muted"
              >
                <i className={clsx('bi', meta.icon, 'text-accent')} aria-hidden />
                {meta.title}
              </h2>
              <p className="text-xs text-ink-faint">{meta.blurb}</p>
            </div>

            <div className="grid gap-3">
              {list.map((cred) => {
                const open = openService === cred.service
                const tier = TIER_META[cred.tier]
                const draft = drafts[cred.service] ?? {}

                return (
                  <GlassCard key={cred.service} className="p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <h3 className="text-sm font-semibold">{cred.displayName}</h3>
                          <Badge tone={tier.tone}>{tier.label}</Badge>
                          <Badge tone={SOURCE_LABEL[cred.source]?.tone ?? 'muted'}>
                            {cred.sharedFrom ? `Via ${cred.sharedFrom}` : SOURCE_LABEL[cred.source]?.label}
                          </Badge>
                          {cred.status === 'VALID' && (
                            <Badge tone="success" icon="bi-patch-check-fill">
                              verified
                            </Badge>
                          )}
                          {cred.status === 'INVALID' && (
                            <Badge tone="danger" icon="bi-x-circle-fill">
                              rejected
                            </Badge>
                          )}
                          {/* Stated, not implied. A disabled key is skipped by `resolve`,
                              so without this the row reads as configured while the module
                              silently uses something else — or nothing. */}
                          {cred.source === 'USER' && !cred.enabled && (
                            <Badge tone="warn" icon="bi-pause-circle">
                              disabled
                            </Badge>
                          )}
                        </div>

                        <p className="mt-1.5 text-xs text-ink-muted">{cred.blurb}</p>
                        <p className="mt-1 text-[11px] text-ink-faint">{cred.costNote}</p>

                        {/* One account can cover several capabilities. Saying so at the
                            point of entry stops someone pasting the same key twice —
                            or worse, concluding a module is unconfigured when it is not. */}
                        {cred.alsoUsedBy.length > 0 && (
                          <p className="mt-1.5 text-[11px] text-ok">
                            <i className="bi bi-link-45deg mr-1" aria-hidden />
                            This key also covers {cred.alsoUsedBy.join(' and ')}.
                          </p>
                        )}
                        {cred.sharedFrom && (
                          <p className="mt-1.5 text-[11px] text-ok">
                            <i className="bi bi-check2-circle mr-1" aria-hidden />
                            Already covered by your {cred.sharedFrom} key — nothing to add here.
                          </p>
                        )}

                        {cred.models.length > 0 && (
                          <p className="mt-1.5 flex flex-wrap gap-1.5">
                            {cred.models.map((model) => (
                              <span
                                key={model}
                                className="rounded border border-edge bg-abyss/[0.06] dark:bg-abyss/40 px-1.5 py-0.5 font-mono text-[10px] text-ink-faint"
                              >
                                {model}
                              </span>
                            ))}
                          </p>
                        )}

                        {Object.keys(cred.hints).length > 0 && (
                          <p className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
                            {Object.entries(cred.hints).map(([key, hint]) => (
                              <span key={key} className="font-mono">
                                {key}: {hint}
                              </span>
                            ))}
                          </p>
                        )}

                        {cred.lastError && (
                          <p className="mt-1.5 text-[11px] text-risk-high">{cred.lastError}</p>
                        )}
                      </div>

                      <div className="flex shrink-0 flex-wrap items-center gap-2">
                        <a
                          href={cred.docsUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                        >
                          Get a key <i className="bi bi-box-arrow-up-right text-[10px]" aria-hidden />
                        </a>
                        {cred.localOnly ? (
                          <Badge tone="accent" icon="bi-hdd">
                            no key needed
                          </Badge>
                        ) : (
                          <>
                            {cred.canVerify && (
                              <Button
                                variant="ghost"
                                icon="bi-patch-check"
                                onClick={() => void verify(cred.service)}
                                loading={busy === cred.service}
                              >
                                Test
                              </Button>
                            )}
                            {cred.source === 'USER' && (
                              <Button
                                variant="ghost"
                                icon={cred.enabled ? 'bi-toggle-on' : 'bi-toggle-off'}
                                onClick={() => void setEnabled(cred.service, !cred.enabled)}
                                disabled={busy === cred.service}
                                title={
                                  cred.enabled
                                    ? 'Skip this provider without deleting the key'
                                    : 'Make this provider available again'
                                }
                              >
                                {cred.enabled ? 'Disable' : 'Enable'}
                              </Button>
                            )}
                            {cred.source === 'USER' && (
                              <Button
                                variant="ghost"
                                icon="bi-trash"
                                onClick={() => void remove(cred.service)}
                                disabled={busy === cred.service}
                              >
                                Remove
                              </Button>
                            )}
                            <Button
                              variant={open ? 'secondary' : 'primary'}
                              icon={open ? 'bi-chevron-up' : 'bi-key'}
                              onClick={() => setOpenService(open ? null : cred.service)}
                              disabled={!encryptionConfigured}
                            >
                              {cred.source === 'USER' ? 'Replace' : 'Add key'}
                            </Button>
                          </>
                        )}
                      </div>
                    </div>

                    {open && !cred.localOnly && (
                      <div className="mt-4 grid gap-3 border-t border-edge pt-4 sm:grid-cols-2">
                        {cred.fields.map((field) => (
                          <Field
                            key={field.key}
                            label={field.label}
                            required={field.required}
                            hint={field.help}
                          >
                            <input
                              // Never pre-filled: there is no read path for a
                              // stored secret, so the hint is a placeholder only.
                              type={field.secret ? 'password' : 'text'}
                              autoComplete="off"
                              spellCheck={false}
                              value={draft[field.key] ?? ''}
                              onChange={(e) =>
                                setDrafts((prev) => ({
                                  ...prev,
                                  [cred.service]: { ...(prev[cred.service] ?? {}), [field.key]: e.target.value },
                                }))
                              }
                              placeholder={cred.hints[field.key] ?? field.placeholder}
                              className={clsx(inputClass, field.secret && 'font-mono')}
                            />
                          </Field>
                        ))}
                        <div className="flex items-end gap-2 sm:col-span-2">
                          <Button
                            variant="primary"
                            icon="bi-lock-fill"
                            onClick={() => void save(cred.service)}
                            loading={busy === cred.service}
                          >
                            Encrypt and save
                          </Button>
                          <Button variant="ghost" onClick={() => setOpenService(null)}>
                            Cancel
                          </Button>
                          <p className="ml-auto text-[11px] text-ink-faint">
                            <i className="bi bi-shield-lock mr-1" aria-hidden />
                            AES-256-GCM, bound to your account
                          </p>
                        </div>
                      </div>
                    )}
                  </GlassCard>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}
