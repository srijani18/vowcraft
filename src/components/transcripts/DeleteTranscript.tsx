'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Button } from '@/components/ui/primitives'
import { Modal } from '@/components/ui/Modal'
import { useToast } from '@/components/ui/Toast'
import { apiFetch } from '@/lib/api-client'
import { apiMessage } from '@/lib/api-error'

/** Deletes one recording from the library — confirmed first, since it takes the
 * transcript's segments and action items with it. */
export function DeleteTranscript({ transcriptId, title }: { transcriptId: string; title: string }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const router = useRouter()
  const toast = useToast()

  const confirmDelete = async () => {
    setBusy(true)
    try {
      const response = await apiFetch(`/api/transcripts/${transcriptId}`, { method: 'DELETE' })
      const json = (await response.json().catch(() => null)) as { ok?: boolean } | null
      if (response.ok && json?.ok) {
        toast.push({ tone: 'success', title: 'Recording deleted' })
        setOpen(false)
        router.refresh()
      } else {
        toast.push({
          tone: 'error',
          title: 'Could not delete recording',
          detail: apiMessage(json, 'The server did not confirm the deletion.'),
        })
      }
    } catch {
      toast.push({ tone: 'error', title: 'Could not reach the server', detail: 'Check your connection and try again.' })
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
        title="Delete this recording?"
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
              {busy ? 'Deleting…' : 'Delete recording'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink-muted">
          This removes the transcript, its segments, and every action item and decision extracted from
          it. This cannot be undone.
        </p>
      </Modal>
    </>
  )
}
