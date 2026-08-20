'use client'

import clsx from 'clsx'
import { useEffect, useRef } from 'react'
import { Button, inputClass } from '@/components/ui/primitives'

/**
 * Filter rail. Every control writes to the URL query string, so a filtered view
 * is shareable and survives a reload (SPEC-001 §8).
 */

export interface FilterState {
  status: string[]
  priority: string[]
  type: string[]
  readiness: string[]
  owner: string
  deadline: string
  q: string
  transcriptId: string
}

export const EMPTY_FILTERS: FilterState = {
  status: [],
  priority: [],
  type: [],
  readiness: [],
  owner: '',
  deadline: '',
  q: '',
  transcriptId: '',
}

export function filtersToSearch(filters: FilterState): string {
  const params = new URLSearchParams()
  for (const key of ['status', 'priority', 'type', 'readiness'] as const) {
    for (const value of filters[key]) params.append(key, value)
  }
  for (const key of ['owner', 'deadline', 'q', 'transcriptId'] as const) {
    if (filters[key]) params.set(key, filters[key])
  }
  return params.toString()
}

export function searchToFilters(params: URLSearchParams): FilterState {
  const multi = (key: string) => params.getAll(key).flatMap((v) => v.split(',')).filter(Boolean)
  return {
    status: multi('status'),
    priority: multi('priority'),
    type: multi('type'),
    readiness: multi('readiness'),
    owner: params.get('owner') ?? '',
    deadline: params.get('deadline') ?? '',
    q: params.get('q') ?? '',
    transcriptId: params.get('transcriptId') ?? '',
  }
}

export function countActive(filters: FilterState): number {
  return (
    filters.status.length +
    filters.priority.length +
    filters.type.length +
    filters.readiness.length +
    (filters.owner ? 1 : 0) +
    (filters.deadline ? 1 : 0) +
    (filters.q ? 1 : 0) +
    (filters.transcriptId ? 1 : 0)
  )
}

const STATUSES = ['PROPOSED', 'APPROVED', 'DEFERRED', 'REJECTED', 'EXECUTED', 'FAILED'] as const
const PRIORITIES = ['HIGH', 'MEDIUM', 'LOW'] as const
const TYPES = ['CALENDAR', 'TASK', 'EMAIL', 'REMINDER', 'NONE'] as const
const READINESS = [
  { value: 'READY', label: 'Ready' },
  { value: 'NEEDS_CLARIFICATION', label: 'Needs clarification' },
  { value: 'INFORMATIONAL', label: 'Informational' },
] as const
const DEADLINES = [
  { value: 'overdue', label: 'Overdue' },
  { value: 'today', label: 'Next 24h' },
  { value: 'week', label: 'This week' },
  { value: 'none', label: 'No deadline' },
] as const

export function Filters({
  filters,
  owners,
  transcripts,
  onChange,
  onClear,
  searchRef,
}: {
  filters: FilterState
  owners: string[]
  transcripts: { id: string; title: string }[]
  onChange(next: FilterState): void
  onClear(): void
  searchRef: React.RefObject<HTMLInputElement | null>
}) {
  const toggle = (key: 'status' | 'priority' | 'type' | 'readiness', value: string) => {
    const set = new Set(filters[key])
    if (set.has(value)) set.delete(value)
    else set.add(value)
    onChange({ ...filters, [key]: [...set] })
  }

  const active = countActive(filters)

  return (
    <div className="panel lit-edge sticky top-0 z-20 -mx-1 mb-5 rounded-2xl px-4 py-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <i
            className="bi bi-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-ink-faint"
            aria-hidden
          />
          <input
            ref={searchRef}
            value={filters.q}
            onChange={(e) => onChange({ ...filters, q: e.target.value })}
            placeholder="Search descriptions and quotes…"
            aria-label="Search action items"
            className={clsx(inputClass, 'pl-8 pr-12')}
          />
          <kbd className="mono-num pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 rounded border border-edge px-1.5 py-0.5 text-ink-faint">
            ⌘K
          </kbd>
        </div>

        <Select
          label="Owner"
          value={filters.owner}
          onChange={(v) => onChange({ ...filters, owner: v })}
          options={owners.map((o) => ({ value: o, label: o }))}
        />
        <Select
          label="Deadline"
          value={filters.deadline}
          onChange={(v) => onChange({ ...filters, deadline: v })}
          options={DEADLINES.map((d) => ({ value: d.value, label: d.label }))}
        />
        {transcripts.length > 1 && (
          <Select
            label="Meeting"
            value={filters.transcriptId}
            onChange={(v) => onChange({ ...filters, transcriptId: v })}
            options={transcripts.map((t) => ({ value: t.id, label: t.title }))}
          />
        )}

        {active > 0 && (
          <Button variant="ghost" icon="bi-x-circle" onClick={onClear}>
            Clear {active}
          </Button>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-edge pt-3">
        <ChipGroup label="Group" values={READINESS.map((r) => ({ value: r.value, label: r.label }))} selected={filters.readiness} onToggle={(v) => toggle('readiness', v)} />
        <ChipGroup label="Status" values={STATUSES.map((s) => ({ value: s, label: title(s) }))} selected={filters.status} onToggle={(v) => toggle('status', v)} />
        <ChipGroup label="Priority" values={PRIORITIES.map((p) => ({ value: p, label: title(p) }))} selected={filters.priority} onToggle={(v) => toggle('priority', v)} />
        <ChipGroup label="Type" values={TYPES.map((t) => ({ value: t, label: title(t) }))} selected={filters.type} onToggle={(v) => toggle('type', v)} />
      </div>
    </div>
  )
}

function title(value: string): string {
  return value.charAt(0) + value.slice(1).toLowerCase().replace(/_/g, ' ')
}

function ChipGroup({
  label,
  values,
  selected,
  onToggle,
}: {
  label: string
  values: { value: string; label: string }[]
  selected: string[]
  onToggle(value: string): void
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-faint">{label}</span>
      {values.map((v) => {
        const on = selected.includes(v.value)
        return (
          <button
            key={v.value}
            onClick={() => onToggle(v.value)}
            aria-pressed={on}
            className={clsx(
              'rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors',
              on
                ? 'border-accent/50 bg-accent/20 text-accent'
                : 'border-edge bg-surface text-ink-muted hover:bg-white/10 hover:text-ink',
            )}
          >
            {v.label}
          </button>
        )
      })}
    </div>
  )
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange(value: string): void
  options: { value: string; label: string }[]
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={label}
      className={clsx(
        'rounded-xl border px-2.5 py-2 text-sm',
        value
          ? 'border-accent/50 bg-accent/15 text-accent'
          : 'border-edge bg-abyss/[0.06] dark:bg-abyss/40 text-ink-muted',
      )}
    >
      <option value="">{label}: any</option>
      {options.map((o) => (
        <option key={o.value} value={o.value} className="bg-surface-strong text-ink">
          {o.label}
        </option>
      ))}
    </select>
  )
}

/** Focuses the search box on ⌘K / Ctrl+K — SPEC-001 §11. */
export function useSearchHotkey(ref: React.RefObject<HTMLInputElement | null>) {
  const bound = useRef(false)
  useEffect(() => {
    if (bound.current) return
    bound.current = true
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        ref.current?.focus()
        ref.current?.select()
      }
    }
    window.addEventListener('keydown', handler)
    return () => {
      window.removeEventListener('keydown', handler)
      bound.current = false
    }
  }, [ref])
}
