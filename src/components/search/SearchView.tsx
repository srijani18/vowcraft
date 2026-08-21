'use client'

import Link from 'next/link'
import { useState } from 'react'
import { Badge, Button, EmptyState, GlassCard, Skeleton, inputClass } from '@/components/ui/primitives'
import { ApiError, apiJson } from '@/lib/api-client'
import type { ReindexResult, SearchHit, SearchResult } from '@/server/search/service'

/**
 * Semantic search screen — SPEC-021.
 *
 * Submit-on-Enter, not live-as-you-type: every query is a real embedding-API call, and
 * burning a token per keystroke would be a strange default for a free tier.
 */

export function SearchView({ voyageConfigured }: { voyageConfigured: boolean }) {
  const [q, setQ] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<SearchHit[] | null>(null)
  const [reindexing, setReindexing] = useState(false)
  const [reindexMessage, setReindexMessage] = useState<string | null>(null)

  const runSearch = async () => {
    if (!q.trim()) return
    setLoading(true)
    setError(null)
    try {
      const data = await apiJson<SearchResult>(`/api/search?q=${encodeURIComponent(q.trim())}`)
      setResults(data.results)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  const runReindex = async () => {
    setReindexing(true)
    setReindexMessage(null)
    try {
      const data = await apiJson<ReindexResult>('/api/search/reindex', { method: 'POST' })
      setReindexMessage(
        data.transcripts > 0
          ? `Reindexing ${data.transcripts} recording${data.transcripts === 1 ? '' : 's'} — this runs in the background.`
          : 'Everything is already indexed.',
      )
    } catch (err) {
      setReindexMessage(err instanceof ApiError ? err.message : 'Could not start reindexing.')
    } finally {
      setReindexing(false)
    }
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Semantic search</h1>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">
          Ask what a meeting said, in your own words — this matches by meaning, not exact wording.
        </p>
      </header>

      {!voyageConfigured && (
        <GlassCard className="border-risk-medium/40 bg-risk-medium/[0.07] p-4">
          <p className="text-sm text-risk-medium">
            <i className="bi bi-exclamation-triangle-fill mr-1.5" aria-hidden />
            Semantic search needs a Voyage AI key — its free tier needs no card.{' '}
            <Link href="/dashboard/settings/credentials" className="underline">
              Add one in Settings → API keys
            </Link>
            .
          </p>
        </GlassCard>
      )}

      <GlassCard className="lit-edge p-4">
        <div className="relative">
          <i
            className="bi bi-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-ink-faint"
            aria-hidden
          />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void runSearch()}
            placeholder="What did they say about the budget?"
            aria-label="Search transcripts by meaning"
            disabled={!voyageConfigured}
            className={inputClass + ' pl-8'}
          />
        </div>
        <div className="mt-3 flex items-center justify-between">
          <Button variant="primary" icon="bi-search" onClick={runSearch} loading={loading} disabled={!voyageConfigured || !q.trim()}>
            Search
          </Button>
          <button
            onClick={runReindex}
            disabled={reindexing || !voyageConfigured}
            className="text-xs text-ink-faint underline decoration-dotted hover:text-ink-muted disabled:opacity-50"
          >
            {reindexing ? 'Reindexing…' : 'Reindex older recordings'}
          </button>
        </div>
        {reindexMessage && <p className="mt-2 text-xs text-ink-muted">{reindexMessage}</p>}
      </GlassCard>

      {loading && (
        <div className="space-y-2">
          {[0, 1, 2].map((n) => (
            <Skeleton key={n} className="h-20 w-full rounded-xl" />
          ))}
        </div>
      )}

      {error && (
        <GlassCard className="border-risk-high/40 bg-risk-high/[0.08] p-4">
          <p className="text-sm text-risk-high">{error}</p>
        </GlassCard>
      )}

      {!loading && results !== null && results.length === 0 && (
        <GlassCard className="p-6">
          <EmptyState icon="bi-search" title="No matches">
            Try different words, or reindex if you added your key after uploading recordings.
          </EmptyState>
        </GlassCard>
      )}

      {!loading && results && results.length > 0 && (
        <ol className="space-y-2">
          {results.map((hit) => (
            <SearchHitRow key={hit.segmentId} hit={hit} />
          ))}
        </ol>
      )}
    </div>
  )
}

function SearchHitRow({ hit }: { hit: SearchHit }) {
  return (
    <li className="panel rounded-xl px-4 py-3">
      <Link
        href={`/dashboard/transcripts/${hit.transcriptId}?t=${hit.startMs}`}
        className="block"
      >
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-faint">
          <span className="font-medium text-ink">{hit.transcriptTitle}</span>
          {hit.speakerLabel && (
            <span>
              <i className="bi bi-person mr-1" aria-hidden />
              {hit.speakerLabel}
            </span>
          )}
          {hit.timestampLabel && (
            <span>
              <i className="bi bi-clock mr-1" aria-hidden />
              {hit.timestampLabel}
            </span>
          )}
          <Badge tone="neutral">{Math.round(hit.score * 100)}% match</Badge>
        </div>
        <div className="mt-1.5 text-sm">
          {hit.contextBefore && <span className="text-ink-faint">{hit.contextBefore} </span>}
          <span className="font-medium">{hit.text}</span>
          {hit.contextAfter && <span className="text-ink-faint"> {hit.contextAfter}</span>}
        </div>
      </Link>
    </li>
  )
}
