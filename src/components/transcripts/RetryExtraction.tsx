'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { Button } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { apiFetch } from '@/lib/api-client'
import { apiMessage } from '@/lib/api-error'

/**
 * Re-runs extraction for one transcript — SPEC-010 §3.4, §8.
 *
 * The Uploader has its own copy of this for the recording it just uploaded, but that
 * panel is gone after a reload, and a failure the user cannot act on the next morning is
 * a failure they cannot act on at all. This one lives on every failed row.
 *
 * Retry is safe to offer repeatedly: `extractInto` replaces only untouched proposals, so
 * approvals and rejections survive, and the transcript itself is never a casualty of a
 * failed extraction.
 */
export function RetryExtraction({ transcriptId }: { transcriptId: string }) {
  const [busy, setBusy] = useState(false)
  const router = useRouter()
  const toast = useToast()

  return (
    <Button
      variant="secondary"
      icon={busy ? 'bi-arrow-repeat' : 'bi-arrow-clockwise'}
      disabled={busy}
      onClick={async () => {
        setBusy(true)
        try {
          const response = await apiFetch(`/api/transcripts/${transcriptId}/extract`, { method: 'POST' })
          const json = (await response.json()) as { ok?: boolean; created?: number }
          if (json.ok) {
            toast.push({
              tone: 'success',
              title:
                json.created === 0
                  ? 'Extraction ran; nothing to act on in this recording'
                  : `Extracted ${json.created} action item${json.created === 1 ? '' : 's'}`,
            })
            router.refresh()
          } else {
            // `error.message` is the same user-facing sentence stored on the row — the
            // provider's own text never reaches here (SPEC-010 §3.4).
            toast.push({
              tone: 'error',
              title: 'Extraction failed again',
              detail: apiMessage(json, 'The provider did not return a result.'),
            })
            router.refresh()
          }
        } catch {
          toast.push({ tone: 'error', title: 'Could not reach the server', detail: 'Check your connection and try again.' })
        } finally {
          setBusy(false)
        }
      }}
    >
      {busy ? 'Extracting…' : 'Retry extraction'}
    </Button>
  )
}
