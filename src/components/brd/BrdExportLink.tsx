'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { apiFetch } from '@/lib/api-client'

/**
 * A download link for a BRD export — SPEC-014 §8.
 *
 * Not a plain `<a href>`: the backend is a different origin from the frontend, so the
 * browser cannot attach a bearer token to a navigation the way it once attached the
 * same-origin session cookie (the same cross-origin consequence the transcript reader's
 * audio/export links hit — see SPEC-015 §7). This fetches the file through the
 * authenticated client instead and hands the browser an object URL to save.
 */
export function BrdExportLink({
  documentId,
  format,
  label,
  icon,
}: {
  documentId: string
  format: 'md' | 'json'
  label: string
  icon: string
}) {
  const [busy, setBusy] = useState(false)
  const toast = useToast()

  return (
    <Button
      variant={format === 'md' ? 'secondary' : 'ghost'}
      icon={icon}
      loading={busy}
      onClick={async () => {
        setBusy(true)
        try {
          const response = await apiFetch(`/api/brd/${documentId}/export?format=${format}`)
          if (!response.ok) {
            // The export route answers errors as plain text, not the JSON envelope — it
            // is meant for a browser navigating a download link.
            const message = await response.text().catch(() => '')
            throw new Error(message.trim() || 'The export failed.')
          }
          const blob = await response.blob()
          const disposition = response.headers.get('content-disposition') ?? ''
          const match = /filename="([^"]+)"/.exec(disposition)
          const filename = match?.[1] ?? `document.${format}`
          const objectUrl = URL.createObjectURL(blob)
          const link = document.createElement('a')
          link.href = objectUrl
          link.download = filename
          link.click()
          URL.revokeObjectURL(objectUrl)
        } catch (err) {
          toast.push({ tone: 'error', title: 'Export failed', detail: (err as Error).message })
        } finally {
          setBusy(false)
        }
      }}
    >
      {label}
    </Button>
  )
}
