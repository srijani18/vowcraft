'use client'

import { useEffect, useMemo, useState } from 'react'
import { Modal } from '@/components/ui/Modal'
import { Button, Field, inputClass } from '@/components/ui/primitives'
import { PAYLOAD_SCHEMA, type FieldSpec } from '@/domain/payload'
import { apiFetch } from '@/lib/api-client'
import type { ActionItemDTO } from '@/server/action-items/dto'

/**
 * Edit modal. Renders the payload form from `PAYLOAD_SCHEMA` rather than a
 * hand-written form per action type — the same table the executor validates
 * against (SPEC-001 §6.1), so the UI cannot offer a field the provider ignores
 * or omit one it requires.
 */

type AttachmentEntry = { kind: string; documentId: string }

/** Only what the picker needs from `/api/brd`'s summary list. */
type DocumentOption = { id: string; title: string; requirementCount: number }

type Draft = {
  description: string
  ownerName: string
  ownerEmail: string
  deadline: string
  priority: string
  actionType: string
  payload: Record<string, string>
}

function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  // datetime-local wants local wall-clock with no zone suffix.
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function payloadToStrings(payload: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(payload)) {
    if (value === null || value === undefined) continue
    if (Array.isArray(value)) out[key] = value.join(', ')
    else if (value instanceof Date) out[key] = value.toISOString()
    else out[key] = String(value)
  }
  return out
}

export function EditModal({
  item,
  open,
  saving,
  onClose,
  onSave,
}: {
  item: ActionItemDTO | null
  open: boolean
  saving: boolean
  onClose(): void
  onSave(patch: Record<string, unknown>): void
}) {
  const [draft, setDraft] = useState<Draft | null>(null)

  useEffect(() => {
    if (!item) return setDraft(null)
    setDraft({
      description: item.description,
      ownerName: item.ownerName ?? '',
      ownerEmail: item.ownerEmail ?? '',
      deadline: toLocalInput(item.deadline),
      priority: item.priority,
      actionType: item.actionType,
      payload: payloadToStrings(item.payload),
    })
  }, [item])

  /*
   * Attachments are structured (`{kind, documentId}`), so they cannot live in `draft.payload`,
   * which is a flat string map for form inputs. Held separately and merged back on submit.
   */
  const [attachments, setAttachments] = useState<AttachmentEntry[]>([])
  const [documents, setDocuments] = useState<DocumentOption[] | null>(null)

  useEffect(() => {
    const existing = item?.payload?.attachments
    setAttachments(
      Array.isArray(existing)
        ? existing.filter(
            (entry): entry is AttachmentEntry =>
              typeof entry === 'object' && entry !== null && 'documentId' in entry,
          )
        : [],
    )
  }, [item])

  // Fetched only when a form that can carry attachments is actually open, so opening a
  // calendar item costs nothing.
  const wantsDocuments = (item?.actionType ?? '') === 'EMAIL'
  useEffect(() => {
    if (!item || !wantsDocuments || documents !== null) return
    let cancelled = false
    apiFetch('/api/brd')
      .then((r) => r.json())
      .then((data: { documents?: DocumentOption[] }) => {
        if (!cancelled) setDocuments(data.documents ?? [])
      })
      .catch(() => {
        // A failed list must not block editing everything else on the item.
        if (!cancelled) setDocuments([])
      })
    return () => {
      cancelled = true
    }
  }, [item, wantsDocuments, documents])

  const specs = useMemo<readonly FieldSpec[]>(
    () => PAYLOAD_SCHEMA[(draft?.actionType ?? 'NONE') as keyof typeof PAYLOAD_SCHEMA] ?? [],
    [draft?.actionType],
  )

  if (!item || !draft) return null

  const setPayload = (key: string, value: string) =>
    setDraft({ ...draft, payload: { ...draft.payload, [key]: value } })

  const submit = () => {
    const payload: Record<string, unknown> = {}
    for (const spec of specs) {
      const raw = draft.payload[spec.key]?.trim() ?? ''
      if (!raw) continue
      if (spec.kind === 'documents') continue // merged in below, from its own state
      if (spec.kind === 'emails') payload[spec.key] = raw.split(/[,;]/).map((s) => s.trim()).filter(Boolean)
      else if (spec.kind === 'number') payload[spec.key] = Number(raw)
      else if (spec.kind === 'datetime') payload[spec.key] = new Date(raw).toISOString()
      else payload[spec.key] = raw
    }
    if (attachments.length > 0) payload.attachments = attachments

    // Carry through keys the schema does not describe (managerApproved, consent
    // flags) so an edit never silently drops state the rule engine reads.
    for (const [key, value] of Object.entries(item.payload)) {
      if (!(key in payload) && !specs.some((s) => s.key === key)) payload[key] = value
    }

    onSave({
      description: draft.description.trim(),
      ownerName: draft.ownerName.trim() || null,
      ownerEmail: draft.ownerEmail.trim() || null,
      deadline: draft.deadline ? new Date(draft.deadline).toISOString() : null,
      priority: draft.priority,
      actionType: draft.actionType,
      payload,
    })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Edit action item"
      subtitle="Your changes are recorded as corrections and improve future extraction."
      icon="bi-pencil-square"
      width="max-w-2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon="bi-check-lg" onClick={submit} loading={saving}>
            Save changes
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Description" required hint="Verb-first, so the task reads as an instruction.">
          <textarea
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            rows={2}
            className={inputClass}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Owner">
            <input
              value={draft.ownerName}
              onChange={(e) => setDraft({ ...draft, ownerName: e.target.value })}
              className={inputClass}
              placeholder="Marcus"
            />
          </Field>
          <Field label="Owner email">
            <input
              type="email"
              value={draft.ownerEmail}
              onChange={(e) => setDraft({ ...draft, ownerEmail: e.target.value })}
              className={inputClass}
              placeholder="marcus@acme.test"
            />
          </Field>
          <Field label="Deadline">
            <input
              type="datetime-local"
              value={draft.deadline}
              onChange={(e) => setDraft({ ...draft, deadline: e.target.value })}
              className={inputClass}
            />
          </Field>
          <Field label="Priority">
            <select
              value={draft.priority}
              onChange={(e) => setDraft({ ...draft, priority: e.target.value })}
              className={inputClass}
            >
              {['HIGH', 'MEDIUM', 'LOW'].map((p) => (
                <option key={p} value={p} className="bg-surface-strong">
                  {p}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <Field
          label="Action type"
          hint="Changing this changes which integration executes it, and which fields are required."
        >
          <select
            value={draft.actionType}
            onChange={(e) => setDraft({ ...draft, actionType: e.target.value })}
            className={inputClass}
          >
            {['CALENDAR', 'TASK', 'EMAIL', 'REMINDER', 'NONE'].map((t) => (
              <option key={t} value={t} className="bg-surface-strong">
                {t}
              </option>
            ))}
          </select>
        </Field>

        {specs.length > 0 && (
          <fieldset className="rounded-xl border border-edge bg-abyss/[0.05] dark:bg-abyss/30 p-4">
            <legend className="px-1.5 text-xs font-medium uppercase tracking-wide text-ink-faint">
              Execution details
            </legend>
            <div className="grid gap-4 sm:grid-cols-2">
              {specs.map((spec) => (
                <div
                  key={spec.key}
                  className={
                    spec.kind === 'text' || spec.kind === 'documents' ? 'sm:col-span-2' : undefined
                  }
                >
                  <Field
                    label={spec.label}
                    required={spec.required}
                    hint={spec.help ?? (spec.kind === 'emails' ? 'Comma separated' : undefined)}
                  >
                    {spec.kind === 'documents' ? (
                      <DocumentPicker
                        documents={documents}
                        selected={attachments}
                        onToggle={(documentId) =>
                          setAttachments((current) =>
                            current.some((a) => a.documentId === documentId)
                              ? current.filter((a) => a.documentId !== documentId)
                              : [...current, { kind: 'brd', documentId }],
                          )
                        }
                      />
                    ) : spec.kind === 'text' ? (
                      <textarea
                        rows={3}
                        value={draft.payload[spec.key] ?? ''}
                        onChange={(e) => setPayload(spec.key, e.target.value)}
                        className={inputClass}
                      />
                    ) : spec.kind === 'enum' ? (
                      <select
                        value={draft.payload[spec.key] ?? ''}
                        onChange={(e) => setPayload(spec.key, e.target.value)}
                        className={inputClass}
                      >
                        <option value="" className="bg-surface-strong">
                          —
                        </option>
                        {spec.options?.map((o) => (
                          <option key={o} value={o} className="bg-surface-strong">
                            {o}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type={
                          spec.kind === 'datetime' ? 'datetime-local' : spec.kind === 'number' ? 'number' : 'text'
                        }
                        value={
                          spec.kind === 'datetime'
                            ? toLocalInput(draft.payload[spec.key] ?? null)
                            : (draft.payload[spec.key] ?? '')
                        }
                        onChange={(e) => setPayload(spec.key, e.target.value)}
                        className={inputClass}
                      />
                    )}
                  </Field>
                </div>
              ))}
            </div>
          </fieldset>
        )}

        {item.sourceQuote && (
          <blockquote className="border-l-2 border-accent/40 bg-abyss/[0.05] dark:bg-abyss/30 px-3 py-2 text-xs italic text-ink-muted">
            “{item.sourceQuote}”
            {item.sourceTimestampLabel && (
              <span className="mono-num ml-2 not-italic text-ink-faint">at {item.sourceTimestampLabel}</span>
            )}
          </blockquote>
        )}
      </div>
    </Modal>
  )
}

/**
 * Explicit selection, deliberately. Extraction can hear "send them the BRD" but cannot know
 * which stored document that is, and this codebase's rule is that a plausible-looking wrong
 * answer is worse than a blank — attaching the wrong requirements document to an outbound
 * email is a disclosure, not a typo. So a spoken mention never becomes a selection; a person
 * picks. The executor re-checks ownership of every id before reading anything.
 */
function DocumentPicker({
  documents,
  selected,
  onToggle,
}: {
  documents: DocumentOption[] | null
  selected: AttachmentEntry[]
  onToggle(documentId: string): void
}) {
  if (documents === null) {
    return <p className="text-xs text-ink-faint">Loading your documents…</p>
  }
  if (documents.length === 0) {
    return (
      <p className="text-xs text-ink-faint">
        No requirements documents yet. Dictate one from the dashboard and it can be attached
        here.
      </p>
    )
  }
  return (
    <div className="max-h-40 space-y-1 overflow-y-auto rounded-xl border border-edge/25 bg-base/60 p-2">
      {documents.map((document) => {
        const checked = selected.some((entry) => entry.documentId === document.id)
        return (
          <label
            key={document.id}
            className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-white/5"
          >
            <input
              type="checkbox"
              checked={checked}
              onChange={() => onToggle(document.id)}
              className="size-3.5 accent-[rgb(var(--accent-fill))]"
            />
            <span className="min-w-0 flex-1 truncate text-xs">{document.title}</span>
            <span className="mono-num shrink-0 text-[10px] text-ink-faint">
              {document.requirementCount} reqs
            </span>
          </label>
        )
      })}
    </div>
  )
}
