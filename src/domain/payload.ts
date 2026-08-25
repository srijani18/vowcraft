import type { ActionType } from './types'

/**
 * Payload field requirements — SPEC-001 §6.1.
 *
 * This table is the single source of truth for "is this executable yet?". The
 * dashboard uses it to render "needs: startsAt, attendees" chips, and
 * VAL_REQUIRED_FIELDS uses it to block execution. One table, so the UI can never
 * promise something the executor will refuse.
 */

export interface FieldSpec {
  key: string
  label: string
  kind: 'string' | 'text' | 'datetime' | 'number' | 'emails' | 'enum' | 'documents'
  required: boolean
  /** Minimum length for array-valued fields such as `attendees`. */
  minItems?: number
  options?: readonly string[]
  help?: string
}

export const PAYLOAD_SCHEMA: Record<ActionType, readonly FieldSpec[]> = {
  CALENDAR: [
    { key: 'title', label: 'Event title', kind: 'string', required: true },
    { key: 'startsAt', label: 'Starts at', kind: 'datetime', required: true },
    { key: 'durationMinutes', label: 'Duration (min)', kind: 'number', required: true },
    { key: 'attendees', label: 'Attendees', kind: 'emails', required: true, minItems: 1 },
    { key: 'location', label: 'Location', kind: 'string', required: false },
    { key: 'description', label: 'Agenda', kind: 'text', required: false },
    { key: 'timeZone', label: 'Time zone', kind: 'string', required: false },
  ],
  TASK: [
    { key: 'title', label: 'Task title', kind: 'string', required: true },
    { key: 'dueAt', label: 'Due', kind: 'datetime', required: false },
    { key: 'assignee', label: 'Assignee', kind: 'string', required: false },
    { key: 'notes', label: 'Notes', kind: 'text', required: false },
    { key: 'projectId', label: 'Project / database id', kind: 'string', required: false },
  ],
  EMAIL: [
    { key: 'to', label: 'To', kind: 'emails', required: true, minItems: 1 },
    { key: 'subject', label: 'Subject', kind: 'string', required: true },
    { key: 'body', label: 'Body', kind: 'text', required: true },
    { key: 'cc', label: 'Cc', kind: 'emails', required: false },
    {
      key: 'sendMode',
      label: 'Send mode',
      kind: 'enum',
      required: false,
      options: ['draft', 'send'] as const,
      help: 'Drafts are LOW risk; sending externally is HIGH risk.',
    },
    {
      // Chosen explicitly, never inferred from the transcript. Extraction can hear "send
      // them the BRD" but cannot know which stored document that is, and attaching the
      // wrong one to an outbound email is a disclosure rather than a typo.
      key: 'attachments',
      label: 'Attachments',
      kind: 'documents',
      required: false,
      help: 'Requirements documents to attach. Ownership is re-checked at execution.',
    },
  ],
  REMINDER: [
    { key: 'message', label: 'Message', kind: 'string', required: true },
    { key: 'remindAt', label: 'Remind at', kind: 'datetime', required: true },
    {
      key: 'channel',
      label: 'Channel',
      kind: 'enum',
      required: true,
      options: ['self', 'slack', 'email'] as const,
    },
    { key: 'target', label: 'Target (channel or address)', kind: 'string', required: false },
  ],
  NONE: [],
}

function isPresent(value: unknown, spec: FieldSpec): boolean {
  if (value === null || value === undefined) return false
  if (Array.isArray(value)) return value.length >= (spec.minItems ?? 1)
  if (typeof value === 'string') return value.trim().length > 0
  if (typeof value === 'number') return Number.isFinite(value)
  return true
}

/** Required payload keys that are absent or empty. Ordered as declared. */
export function missingFields(
  actionType: ActionType,
  payload: Record<string, unknown>,
): string[] {
  return PAYLOAD_SCHEMA[actionType]
    .filter((spec) => spec.required && !isPresent(payload[spec.key], spec))
    .map((spec) => spec.key)
}

export function fieldLabel(actionType: ActionType, key: string): string {
  return PAYLOAD_SCHEMA[actionType].find((f) => f.key === key)?.label ?? key
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/

export function isValidEmail(value: unknown): value is string {
  return typeof value === 'string' && EMAIL_RE.test(value.trim())
}

/** Normalises an `emails`-kind field to a trimmed, lowercased, deduped array. */
export function toEmailList(value: unknown): string[] {
  const raw =
    Array.isArray(value) ? value
    : typeof value === 'string' ? value.split(/[,;]/)
    : []
  const seen = new Set<string>()
  for (const entry of raw) {
    if (typeof entry !== 'string') continue
    const normalised = entry.trim().toLowerCase()
    if (normalised) seen.add(normalised)
  }
  return [...seen]
}

export function emailDomain(address: string): string | null {
  const at = address.lastIndexOf('@')
  return at === -1 ? null : address.slice(at + 1).toLowerCase()
}
