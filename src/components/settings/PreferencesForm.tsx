'use client'

import clsx from 'clsx'
import { useState } from 'react'
import { Badge, Button, Field, GlassCard, inputClass } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { apiFetch } from '@/lib/api-client'
import type { SettingsView } from '@/server/settings/service'

/**
 * Preferences — SPEC-005 §4.1.
 *
 * Every control here feeds the pure rule engine, so this screen *is* the
 * guardrail envelope. Each field therefore names the rule id it governs: a
 * settings page full of unexplained knobs invites people to change things they do
 * not understand, and these particular knobs decide what the agent may do.
 */

const GATES = [
  { value: '', label: 'Default for its risk tier' },
  { value: 'EXPLICIT_APPROVAL', label: 'Always require approval' },
  { value: 'EXPLICIT_APPROVAL_WITH_CONFIRMATION', label: 'Require approval + typed confirmation' },
] as const

const ACTION_TYPES = ['CALENDAR', 'TASK', 'EMAIL', 'REMINDER'] as const

export function PreferencesForm({ initial }: { initial: SettingsView }) {
  const toast = useToast()
  const [settings, setSettings] = useState(initial)
  const [draft, setDraft] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [domainInput, setDomainInput] = useState('')

  const dirty = JSON.stringify(draft) !== JSON.stringify(settings)

  const set = <K extends keyof SettingsView>(key: K, value: SettingsView[K]) =>
    setDraft((prev) => ({ ...prev, [key]: value }))

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const response = await apiFetch('/api/settings', {
        method: 'PATCH',
        body: JSON.stringify({
          timeZone: draft.timeZone,
          workdayStart: draft.workdayStart,
          workdayEnd: draft.workdayEnd,
          allowWeekends: draft.allowWeekends,
          maxMeetingMinutes: draft.maxMeetingMinutes,
          minBufferMinutes: draft.minBufferMinutes,
          orgDomains: draft.orgDomains,
          orgCurrency: draft.orgCurrency,
          budgetApprovalLimit: draft.budgetApprovalLimit,
          autoExecuteLowRisk: draft.autoExecuteLowRisk,
          approvalThresholds: draft.approvalThresholds,
          providerRouting: draft.providerRouting,
        }),
      })
      const json = (await response.json()) as SettingsView & { error?: { message: string } }
      if (!response.ok) throw new Error(json.error?.message ?? 'Could not save')
      setSettings(json)
      setDraft(json)
      toast.push({
        tone: 'success',
        title: 'Preferences saved',
        detail: 'The guardrails use these immediately — no restart needed.',
      })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const addDomain = () => {
    const value = domainInput.trim().toLowerCase().replace(/^@/, '')
    if (!value || draft.orgDomains.includes(value)) return
    set('orgDomains', [...draft.orgDomains, value])
    setDomainInput('')
  }

  return (
    <div className="space-y-6 pb-24">
      <header>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Preferences</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          These settings are the guardrail envelope. Every field below is read by the rule engine on
          every execution attempt, and each one names the rule it governs.
        </p>
      </header>

      {/* ── working hours */}
      <GlassCard className="p-5">
        <SectionHead
          icon="bi-clock"
          title="Working hours"
          blurb="When a meeting may be scheduled."
          rules={['SCHED_HOURS', 'SCHED_WEEKEND', 'SCHED_PAST']}
        />

        <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Time zone" hint="Every scheduling rule is evaluated in this zone.">
            <select
              value={draft.timeZone}
              onChange={(e) => set('timeZone', e.target.value)}
              className={inputClass}
            >
              {draft.timeZones.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Day starts" hint="HH:mm">
            <input
              type="time"
              value={draft.workdayStart}
              onChange={(e) => set('workdayStart', e.target.value)}
              className={inputClass}
            />
          </Field>

          <Field label="Day ends" hint="HH:mm">
            <input
              type="time"
              value={draft.workdayEnd}
              onChange={(e) => set('workdayEnd', e.target.value)}
              className={inputClass}
            />
          </Field>

          <Toggle
            label="Allow weekend meetings"
            checked={draft.allowWeekends}
            onChange={(v) => set('allowWeekends', v)}
            hint={draft.allowWeekends ? 'SCHED_WEEKEND will not fire.' : 'Saturday and Sunday are blocked.'}
          />
        </div>
      </GlassCard>

      {/* ── meetings */}
      <GlassCard className="p-5">
        <SectionHead
          icon="bi-calendar-range"
          title="Meeting limits"
          blurb="How long, and how tightly packed."
          rules={['SCHED_MAX_DURATION', 'SCHED_BUFFER', 'SCHED_CONFLICT']}
        />

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field
            label="Maximum meeting length"
            hint="Anything longer is blocked outright, not merely flagged."
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={5}
                max={1440}
                value={draft.maxMeetingMinutes}
                onChange={(e) => set('maxMeetingMinutes', Number(e.target.value))}
                className={inputClass}
              />
              <span className="shrink-0 text-xs text-ink-faint">minutes</span>
            </div>
          </Field>

          <Field label="Preferred buffer between meetings" hint="A warning, not a block — you can override it.">
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={240}
                value={draft.minBufferMinutes}
                onChange={(e) => set('minBufferMinutes', Number(e.target.value))}
                className={inputClass}
              />
              <span className="shrink-0 text-xs text-ink-faint">minutes</span>
            </div>
          </Field>
        </div>
      </GlassCard>

      {/* ── organisation */}
      <GlassCard className="p-5">
        <SectionHead
          icon="bi-building"
          title="Organisation"
          blurb="What counts as internal, and when money needs a second pair of eyes."
          rules={['POL_EXTERNAL_EMAIL', 'VAL_BUDGET_APPROVAL']}
        />

        <div className="mt-4 space-y-4">
          <Field
            label="Internal email domains"
            hint="A recipient outside these domains makes an email HIGH risk, requiring a typed confirmation."
          >
            <div className="flex flex-wrap items-center gap-1.5">
              {draft.orgDomains.map((domain) => (
                <span
                  key={domain}
                  className="inline-flex items-center gap-1.5 rounded-full border border-edge/25 bg-surface-strong/60 px-2.5 py-1 text-xs"
                >
                  @{domain}
                  <button
                    onClick={() => set('orgDomains', draft.orgDomains.filter((d) => d !== domain))}
                    aria-label={`Remove ${domain}`}
                    className="text-ink-faint hover:text-danger"
                  >
                    <i className="bi bi-x text-[11px]" aria-hidden />
                  </button>
                </span>
              ))}
              {draft.orgDomains.length === 0 && (
                <span className="text-xs text-warn">
                  <i className="bi bi-exclamation-triangle mr-1" aria-hidden />
                  With none set, every recipient counts as external.
                </span>
              )}
            </div>
            <div className="mt-2 flex gap-2">
              <input
                value={domainInput}
                onChange={(e) => setDomainInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addDomain()
                  }
                }}
                placeholder="acme.com"
                className={inputClass}
              />
              <Button variant="secondary" icon="bi-plus-lg" onClick={addDomain}>
                Add
              </Button>
            </div>
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Currency" hint="Three-letter code.">
              <input
                value={draft.orgCurrency}
                onChange={(e) => set('orgCurrency', e.target.value.toUpperCase().slice(0, 3))}
                maxLength={3}
                className={clsx(inputClass, 'font-mono uppercase')}
              />
            </Field>

            <Field
              label="Amount needing manager sign-off"
              hint="An action mentioning more than this becomes HIGH risk and is blocked until approved."
            >
              <input
                type="number"
                min={0}
                value={draft.budgetApprovalLimit}
                onChange={(e) => set('budgetApprovalLimit', Number(e.target.value))}
                className={inputClass}
              />
            </Field>
          </div>
        </div>
      </GlassCard>

      {/* ── automation */}
      <GlassCard className={clsx('p-5', draft.autoExecuteLowRisk && 'border-warn/50')}>
        <SectionHead
          icon="bi-robot"
          title="Automation"
          blurb="How much the agent may do without asking."
          rules={['effectiveGate']}
        />

        <div
          className={clsx(
            'mt-4 rounded-xl border p-4',
            draft.autoExecuteLowRisk ? 'border-warn/50 bg-warn/[0.08]' : 'border-edge/25 bg-surface-strong/40',
          )}
        >
          <Toggle
            label="Auto-execute low-risk, high-confidence actions"
            checked={draft.autoExecuteLowRisk}
            onChange={(v) => set('autoExecuteLowRisk', v)}
            hint={
              draft.autoExecuteLowRisk
                ? 'Drafts, private notes, and self-only calendar holds will run without asking you first. Medium and high risk still require approval — that cannot be turned off.'
                : 'Off. Everything waits for your explicit approval. This is the default, and the safer setting.'
            }
            tone={draft.autoExecuteLowRisk ? 'warn' : 'neutral'}
          />
        </div>

        <div className="mt-4">
          <p className="mb-2 text-xs font-medium text-ink-muted">Per action type, require at least</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {ACTION_TYPES.map((type) => (
              <Field key={type} label={type.charAt(0) + type.slice(1).toLowerCase()}>
                <select
                  value={draft.approvalThresholds[type] ?? ''}
                  onChange={(e) =>
                    set('approvalThresholds', {
                      ...draft.approvalThresholds,
                      ...(e.target.value
                        ? { [type]: e.target.value as never }
                        : { [type]: undefined as never }),
                    })
                  }
                  className={inputClass}
                >
                  {GATES.map((gate) => (
                    <option key={gate.value} value={gate.value}>
                      {gate.label}
                    </option>
                  ))}
                </select>
              </Field>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-ink-faint">
            <i className="bi bi-info-circle mr-1" aria-hidden />
            These can only tighten the computed gate. There is no option here that would let something
            through more easily than its risk tier allows.
          </p>
        </div>
      </GlassCard>

      {/* ── routing */}
      {draft.routingOptions.length > 0 && (
        <GlassCard className="p-5">
          <SectionHead
            icon="bi-signpost-2"
            title="Provider routing"
            blurb="Which integration executes each kind of action."
            rules={['resolveProvider']}
          />

          <div className="mt-4 space-y-4">
            {draft.routingOptions.map((option) => (
              <fieldset key={option.capability}>
                <legend className="mb-2 text-xs font-medium text-ink-muted">
                  {option.capability.charAt(0) + option.capability.slice(1).toLowerCase()} actions
                </legend>
                <div className="grid gap-2 sm:grid-cols-2">
                  {option.providers.map((provider) => {
                    const selected =
                      (draft.providerRouting[option.capability as keyof typeof draft.providerRouting] ??
                        option.providers.find((p) => p.isDefault)?.id) === provider.id
                    return (
                      <label
                        key={provider.id}
                        className={clsx(
                          'flex cursor-pointer items-start gap-2.5 rounded-xl border p-3 transition-colors',
                          selected
                            ? 'border-accent-fill bg-accent-fill/10'
                            : 'border-edge/25 bg-surface-strong/40 hover:border-edge/50',
                        )}
                      >
                        <input
                          type="radio"
                          name={`routing-${option.capability}`}
                          checked={selected}
                          onChange={() =>
                            set('providerRouting', {
                              ...draft.providerRouting,
                              [option.capability]: provider.id,
                            })
                          }
                          className="mt-0.5 size-4 accent-accent-fill"
                        />
                        <span className="min-w-0">
                          <span className="flex items-center gap-2 text-sm font-medium">
                            {provider.displayName}
                            {provider.isDefault && (
                              <Badge tone="muted">default</Badge>
                            )}
                          </span>
                          <span className="mt-0.5 block text-[11px] leading-relaxed text-ink-muted">
                            {provider.note}
                          </span>
                        </span>
                      </label>
                    )
                  })}
                </div>
              </fieldset>
            ))}
          </div>
        </GlassCard>
      )}

      {error && (
        <p className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          <i className="bi bi-exclamation-triangle-fill mr-1.5" aria-hidden />
          {error}
        </p>
      )}

      {/* Sticky save bar: this form is long, and a save button at the bottom of a
          long form is a save button people do not find. */}
      {dirty && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-edge/25 bg-base/95 px-4 py-3 backdrop-blur-xl sm:px-6">
          <div className="mx-auto flex max-w-[1500px] items-center gap-3">
            <p className="text-xs text-ink-muted">
              <i className="bi bi-pencil-square mr-1.5 text-accent" aria-hidden />
              Unsaved changes to the guardrail envelope.
            </p>
            <div className="ml-auto flex items-center gap-2">
              <Button variant="ghost" onClick={() => setDraft(settings)} disabled={saving}>
                Discard
              </Button>
              <Button variant="primary" icon="bi-check-lg" onClick={save} loading={saving}>
                Save preferences
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function SectionHead({
  icon,
  title,
  blurb,
  rules,
}: {
  icon: string
  title: string
  blurb: string
  rules: string[]
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          <i className={clsx('bi', icon, 'mr-2 text-accent')} aria-hidden />
          {title}
        </h2>
        <p className="mt-1 text-xs text-ink-faint">{blurb}</p>
      </div>
      <div className="flex flex-wrap gap-1">
        {rules.map((rule) => (
          <span
            key={rule}
            title="The guardrail this setting controls"
            className="mono-num rounded border border-edge/25 px-1.5 py-0.5 text-ink-faint"
          >
            {rule}
          </span>
        ))}
      </div>
    </div>
  )
}

function Toggle({
  label,
  checked,
  onChange,
  hint,
  tone = 'neutral',
}: {
  label: string
  checked: boolean
  onChange(value: boolean): void
  hint?: string
  tone?: 'neutral' | 'warn'
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors',
          checked ? (tone === 'warn' ? 'bg-warn' : 'bg-accent-fill') : 'bg-ink-faint/40',
        )}
      >
        <span
          className={clsx(
            'absolute top-0.5 size-4 rounded-full bg-base transition-all',
            checked ? 'left-[1.125rem]' : 'left-0.5',
          )}
        />
      </button>
      <span className="min-w-0">
        <span className="block text-xs font-medium text-ink-muted">{label}</span>
        {hint && <span className="mt-0.5 block text-[11px] leading-relaxed text-ink-faint">{hint}</span>}
      </span>
    </label>
  )
}
