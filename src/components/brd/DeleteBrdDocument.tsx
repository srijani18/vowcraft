'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Button } from '@/components/ui/primitives'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { apiFetch } from '@/lib/api-client'
import { apiMessage } from '@/lib/api-error'

/**
 * Deletes one requirements document — confirmed first, because it takes the document's
 * whole revision history with it (SPEC-014 §8).
 *
 * Deliberately the same shape as `DeleteTranscript`: two destructive row actions that
 * behaved differently would be the more confusing outcome.
 */
export function DeleteBrdDocument({
  documentId,
  title,
  revisionCount,
}: {
  documentId: string
  title: string
  revisionCount: number
}) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const router = useRouter()
  const toast = useToast()

  const confirmDelete = async () => {
    setBusy(true)
    try {
      const response = await apiFetch(`/api/brd/${documentId}`, { method: 'DELETE' })
      const json = (await response.json().catch(() => null)) as { ok?: boolean } | null
      if (response.ok && json?.ok) {
        toast.push({ tone: 'success', title: 'Document deleted' })
        setOpen(false)
        router.refresh()
      } else {
        toast.push({
          tone: 'error',
          title: 'Could not delete document',
          detail: apiMessage(json, 'The server did not confirm the deletion.'),
        })
      }
    } catch {
      toast.push({
        tone: 'error',
        title: 'Could not reach the server',
        detail: 'Check your connection and try again.',
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button
        variant="danger"
        icon="bi-trash"
        aria-label={`Delete ${title}`}
        onClick={() => setOpen(true)}
      >
        Delete
      </Button>

      <Modal
        open={open}
        onClose={() => !busy && setOpen(false)}
        title="Delete this document?"
        subtitle={title}
        icon="bi-trash"
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="danger"
              icon={busy ? 'bi-arrow-repeat' : 'bi-trash'}
              disabled={busy}
              onClick={confirmDelete}
            >
              {busy ? 'Deleting…' : 'Delete document'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-muted">
          This removes the document and{' '}
          {revisionCount === 1 ? 'its single revision' : `all ${revisionCount} of its revisions`} —
          including the record of what you said at each turn. This cannot be undone.
        </p>
      </Modal>
    </>
  )
}
