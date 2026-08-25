'use client'

import { useState } from 'react'
import { Badge, Button, EmptyState, Field, GlassCard, inputClass } from '@/components/ui/primitives'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { apiFetch } from '@/lib/api-client'
import { apiErrorCode, apiMessage } from '@/lib/api-error'
import type { TeamMemberView } from '@/server/team/service'

/**
 * The team roster — SPEC-005 §4.2.
 *
 * Worth being clear about in the UI, because the name invites the wrong assumption: this is
 * an address book, not an access list. Adding someone grants them nothing and does not
 * create an account. What it does is let a spoken name resolve to an address, which is what
 * makes an action executable — "email Priya" cannot run until "Priya" is a real recipient.
 */
export function TeamRoster({ initial }: { initial: TeamMemberView[] }) {
  const [members, setMembers] = useState(initial)
  const toast = useToast()

  const [editing, setEditing] = useState<TeamMemberView | null>(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<TeamMemberView | null>(null)
  const [busy, setBusy] = useState(false)

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('')
  const [fieldError, setFieldError] = useState<{ field: string; message: string } | null>(null)

  const openAdd = () => {
    setName('')
    setEmail('')
    setRole('')
    setFieldError(null)
    setAdding(true)
  }

  const openEdit = (member: TeamMemberView) => {
    setName(member.name)
    setEmail(member.email)
    setRole(member.role ?? '')
    setFieldError(null)
    setEditing(member)
  }

  const closeForm = () => {
    if (busy) return
    setAdding(false)
    setEditing(null)
    setFieldError(null)
  }

  const reload = async () => {
    const data = await apiFetch('/api/team').then((r) => r.json())
    setMembers((data as { members: TeamMemberView[] }).members ?? [])
  }

  const save = async () => {
    setBusy(true)
    setFieldError(null)
    const isEdit = editing !== null
    try {
      const response = await apiFetch(isEdit ? `/api/team/${editing.id}` : '/api/team', {
        method: isEdit ? 'PATCH' : 'POST',
        // `role` is always sent, so clearing it is expressible — the API distinguishes an
        // absent field ("leave it") from an explicit null ("clear it").
        body: JSON.stringify({ name, email, role: role.trim() ? role.trim() : null }),
      })
      const json = (await response.json().catch(() => null)) as unknown
      if (!response.ok) {
        const code = apiErrorCode(json)
        const message = apiMessage(json, 'Could not save.')
        // The API names the offending field, so highlight the input rather than showing a
        // banner the user has to map back to a box themselves.
        const field =
          code === 'invalid_email' || code === 'duplicate_email'
            ? 'email'
            : code === 'invalid_name'
              ? 'name'
              : code === 'invalid_role'
                ? 'role'
                : null
        if (field) setFieldError({ field, message })
        else toast.push({ tone: 'error', title: 'Could not save', detail: message })
        return
      }
      await reload()
      toast.push({
        tone: 'success',
        title: isEdit ? 'Person updated' : 'Person added',
        detail: 'Actions naming them can now resolve to this address.',
      })
      setAdding(false)
      setEditing(null)
    } catch {
      toast.push({ tone: 'error', title: 'Could not reach the server' })
    } finally {
      setBusy(false)
    }
  }

  const confirmRemove = async () => {
    if (!removing) return
    setBusy(true)
    try {
      const response = await apiFetch(`/api/team/${removing.id}`, { method: 'DELETE' })
      if (response.ok) {
        await reload()
        toast.push({ tone: 'success', title: 'Removed from the roster' })
        setRemoving(null)
      } else {
        const json = (await response.json().catch(() => null)) as unknown
        toast.push({
          tone: 'error',
          title: 'Could not remove',
          detail: apiMessage(json, 'The server did not confirm the removal.'),
        })
      }
    } catch {
      toast.push({ tone: 'error', title: 'Could not reach the server' })
    } finally {
      setBusy(false)
    }
  }

  const errorFor = (field: string) =>
    fieldError?.field === field ? fieldError.message : undefined

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Team roster</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-muted">
            The people you mention in meetings, and the addresses they resolve to. When a
            recording says &ldquo;send it to Priya&rdquo;, this is what turns that into a real
            recipient — without it, the action has nobody to act on.
          </p>
          <p className="mt-2 max-w-2xl text-xs text-ink-faint">
            <i className="bi bi-info-circle mr-1.5" aria-hidden />
            This is an address book, not an access list. Adding someone grants no access and
            does not create an account for them.
          </p>
        </div>
        <Button variant="primary" icon="bi-person-plus" onClick={openAdd}>
          Add person
        </Button>
      </header>

      {members.length === 0 ? (
        <GlassCard className="p-10">
          <EmptyState icon="bi-people" title="Nobody on the roster yet">
            Add the colleagues you talk about in meetings. Until then, an action assigned to
            someone by name has no address to execute against.
            <div className="mt-4">
              <Button variant="accent" icon="bi-person-plus" onClick={openAdd}>
                Add the first person
              </Button>
            </div>
          </EmptyState>
        </GlassCard>
      ) : (
        <div className="space-y-2">
          {members.map((member) => (
            <GlassCard key={member.id} className="p-3.5 sm:p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-medium">{member.name}</p>
                    {member.role && (
                      <Badge tone="neutral" icon="bi-briefcase">
                        {member.role}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-0.5 truncate text-xs text-ink-muted">
                    <i className="bi bi-envelope mr-1.5" aria-hidden />
                    {member.email}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button variant="secondary" icon="bi-pencil" onClick={() => openEdit(member)}>
                    Edit
                  </Button>
                  <Button
                    variant="danger"
                    icon="bi-trash"
                    aria-label={`Remove ${member.name}`}
                    onClick={() => setRemoving(member)}
                  >
                    Remove
                  </Button>
                </div>
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      <Modal
        open={adding || editing !== null}
        onClose={closeForm}
        title={editing ? 'Edit person' : 'Add person'}
        subtitle="Their name as it is spoken in meetings, and the address actions should use."
        icon={editing ? 'bi-pencil' : 'bi-person-plus'}
        footer={
          <>
            <Button variant="secondary" onClick={closeForm} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="primary"
              icon={busy ? 'bi-arrow-repeat' : 'bi-check-lg'}
              disabled={busy || !name.trim() || !email.trim()}
              onClick={save}
            >
              {busy ? 'Saving…' : editing ? 'Save changes' : 'Add to roster'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field
            label="Name"
            required
            error={errorFor('name')}
            hint="Match how they are referred to out loud — that is what extraction matches on."
          >
            <input
              className={inputClass}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Anjali Pandey"
              autoComplete="off"
            />
          </Field>
          <Field label="Email" required error={errorFor('email')}>
            <input
              className={inputClass}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="anjali@acme.test"
              type="email"
              autoComplete="off"
            />
          </Field>
          <Field label="Role" error={errorFor('role')} hint="Optional. Shown for context only.">
            <input
              className={inputClass}
              value={role}
              onChange={(event) => setRole(event.target.value)}
              placeholder="Product"
              autoComplete="off"
            />
          </Field>
        </div>
      </Modal>

      <Modal
        open={removing !== null}
        onClose={() => !busy && setRemoving(null)}
        title="Remove from the roster?"
        subtitle={removing?.name}
        icon="bi-trash"
        footer={
          <>
            <Button variant="secondary" onClick={() => setRemoving(null)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="danger"
              icon={busy ? 'bi-arrow-repeat' : 'bi-trash'}
              disabled={busy}
              onClick={confirmRemove}
            >
              {busy ? 'Removing…' : 'Remove'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-muted">
          Future recordings that mention {removing?.name ?? 'them'} will no longer resolve to
          an address. Action items already created keep the address they were given.
        </p>
      </Modal>
    </div>
  )
}
