'use client'

import clsx from 'clsx'
import Link from 'next/link'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Badge, Button, GlassCard, inputClass } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/Toast'
import { apiFetch } from '@/lib/api-client'
import { formatTimestamp } from '@/lib/time'
import type { TranscriptDetail } from '@/server/ingest/service'

/**
 * The transcript reader — SPEC-012.
 *
 * Its real job is closing the grounding loop: an action item cites a timestamp and a
 * quote, and until now a reviewer had to take both on trust. Arriving here with `?t=`
 * puts the cursor on that sentence so it can be read and heard.
 *
 * ── Why the highlight is imperative
 *
 * A 90-minute meeting is roughly 15,000 words and `timeupdate` fires about four times a
 * second. Re-rendering 15,000 React nodes 4×/s is not viable, and memoising each word
 * still costs a full reconciliation pass. So the active word is found by binary search
 * over a flat pre-sorted array (O(log n)) and applied by toggling a class on exactly two
 * DOM nodes — the one leaving and the one entering. React owns the structure; the
 * ticking highlight does not go through it.
 */

interface Props {
  transcript: TranscriptDetail
  /** Millisecond offset to open at, from an action item's citation. */
  initialAtMs?: number
  initialQuery?: string
}

/** A word flattened out of the segment tree, with the indices needed to address it. */
interface FlatWord {
  startMs: number
  endMs: number
  segmentIndex: number
}

const WORD_ACTIVE_CLASS = 'is-spoken'

export function TranscriptReader({ transcript, initialAtMs, initialQuery }: Props) {
  const toast = useToast()
  const audioRef = useRef<HTMLAudioElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const [playing, setPlaying] = useState(false)
  const [currentMs, setCurrentMs] = useState(initialAtMs ?? 0)
  const [follow, setFollow] = useState(true)
  const [rate, setRate] = useState(1)
  const [query, setQuery] = useState(initialQuery ?? '')
  const [matchIndex, setMatchIndex] = useState(0)
  const [speakers, setSpeakers] = useState(transcript.speakers)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [draftName, setDraftName] = useState('')
  const [audioSrc, setAudioSrc] = useState<string | null>(null)
  const [exporting, setExporting] = useState<string | null>(null)

  /*
   * The backend is a different origin from the frontend, so the browser will not attach
   * the bearer token to a plain `<audio src>` the way it once attached the same-origin
   * session cookie. Fetching the audio through the authenticated client and handing the
   * player an object URL is the standard workaround — seeking then happens against the
   * in-memory blob, so it costs nothing extra despite the backend's own Range support
   * going unused by this particular caller.
   */
  useEffect(() => {
    let objectUrl: string | null = null
    let cancelled = false
    apiFetch(`/api/transcripts/${transcript.id}/audio`)
      .then((response) => (response.ok ? response.blob() : Promise.reject(new Error('audio fetch failed'))))
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setAudioSrc(objectUrl)
      })
      .catch(() => {
        if (!cancelled) toast.push({ tone: 'error', title: 'Could not load the audio' })
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transcript.id])

  const hasWords = transcript.segments.some((s) => s.words.length > 0)

  /*
   * One flat, sorted array of every word. Built once: the binary search on each tick
   * needs contiguous memory, not a nested walk.
   */
  const flatWords = useMemo<FlatWord[]>(() => {
    const flat: FlatWord[] = []
    transcript.segments.forEach((segment, segmentIndex) => {
      for (const word of segment.words) {
        flat.push({ startMs: word.startMs, endMs: word.endMs, segmentIndex })
      }
    })
    return flat
  }, [transcript.segments])

  /** Index of the word being spoken at `ms`, or -1. O(log n). */
  const findWordAt = useCallback(
    (ms: number): number => {
      let low = 0
      let high = flatWords.length - 1
      let best = -1
      while (low <= high) {
        const mid = (low + high) >> 1
        const word = flatWords[mid]!
        if (ms < word.startMs) {
          high = mid - 1
        } else {
          // At or past this word's start: a candidate, but a later one may be better.
          best = mid
          low = mid + 1
        }
      }
      // Guard the tail: past the last word's end, nothing is being spoken.
      if (best >= 0 && ms > (flatWords[best]!.endMs ?? 0) + 400) return -1
      return best
    },
    [flatWords],
  )

  const activeWordRef = useRef(-1)

  /** Moves the highlight by touching two nodes. Never re-renders the word list. */
  const paintWord = useCallback(
    (index: number) => {
      if (index === activeWordRef.current) return
      const root = scrollRef.current
      if (!root) return

      const previous = activeWordRef.current
      if (previous >= 0) {
        const node = root.querySelector<HTMLElement>(`[data-w="${previous}"]`)
        node?.classList.remove(WORD_ACTIVE_CLASS)
        node?.removeAttribute('aria-current')
      }
      if (index >= 0) {
        const node = root.querySelector<HTMLElement>(`[data-w="${index}"]`)
        node?.classList.add(WORD_ACTIVE_CLASS)
        node?.setAttribute('aria-current', 'true')
        if (follow) {
          // `nearest` rather than `center`: centring yanks the page on every word.
          node?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
        }
      }
      activeWordRef.current = index
    },
    [follow],
  )

  // ── playback wiring
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    const onTime = () => {
      const ms = Math.round(audio.currentTime * 1000)
      setCurrentMs(ms)
      paintWord(findWordAt(ms))
    }
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnded = () => {
      setPlaying(false)
      paintWord(-1)
    }

    audio.addEventListener('timeupdate', onTime)
    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnded)
    return () => {
      audio.removeEventListener('timeupdate', onTime)
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnded)
    }
    // `audioSrc` starts null (the element itself is unmounted until the authenticated
    // fetch resolves), so this must re-run once the element actually mounts.
  }, [findWordAt, paintWord, audioSrc])

  const seek = useCallback(
    (ms: number, options: { play?: boolean } = {}) => {
      const audio = audioRef.current
      if (!audio) return
      audio.currentTime = Math.max(0, ms / 1000)
      setCurrentMs(ms)
      paintWord(findWordAt(ms))
      // A seek is an explicit statement about where attention should be, so it re-enables
      // following even if the reader had scrolled away.
      setFollow(true)
      if (options.play) void audio.play().catch(() => undefined)
    },
    [findWordAt, paintWord],
  )

  // Open at the cited moment, once the audio knows its duration.
  const openedAt = useRef(false)
  useEffect(() => {
    const audio = audioRef.current
    if (!audio || openedAt.current || !initialAtMs) return
    const apply = () => {
      openedAt.current = true
      // Deliberately paused: land on the sentence, let the reader decide to play it.
      seek(initialAtMs)
    }
    if (audio.readyState >= 1) apply()
    else audio.addEventListener('loadedmetadata', apply, { once: true })
  }, [initialAtMs, seek, audioSrc])

  // Manual scrolling suspends following — auto-scroll that fights the user is the most
  // irritating thing a player like this can do.
  useEffect(() => {
    const root = scrollRef.current
    if (!root) return
    let programmatic = false
    const onScroll = () => {
      if (programmatic) return
      setFollow(false)
    }
    // `scrollIntoView` also fires scroll; a short suppression window separates the two.
    const suppress = () => {
      programmatic = true
      window.setTimeout(() => {
        programmatic = false
      }, 700)
    }
    root.addEventListener('scroll', onScroll, { passive: true })
    root.addEventListener('v2b:autoscroll', suppress)
    return () => {
      root.removeEventListener('scroll', onScroll)
      root.removeEventListener('v2b:autoscroll', suppress)
    }
  }, [])

  // ── keyboard, disabled while typing
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      const audio = audioRef.current
      if (!audio) return

      const key = event.key.toLowerCase()
      if (key === ' ' || key === 'k') {
        event.preventDefault()
        if (audio.paused) void audio.play().catch(() => undefined)
        else audio.pause()
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        seek(currentMs + 5_000, { play: playing })
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        seek(Math.max(0, currentMs - 5_000), { play: playing })
      } else if (key === 'l') {
        event.preventDefault()
        seek(currentMs + 10_000, { play: playing })
      } else if (key === 'j') {
        event.preventDefault()
        seek(Math.max(0, currentMs - 10_000), { play: playing })
      } else if (key === 'f') {
        event.preventDefault()
        setFollow((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [currentMs, playing, seek])

  // ── search: substring, not semantic, and the UI does not pretend otherwise
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (needle.length < 2) return []
    return transcript.segments
      .map((segment, index) => ({ index, hit: segment.text.toLowerCase().includes(needle) }))
      .filter((m) => m.hit)
      .map((m) => m.index)
  }, [query, transcript.segments])

  useEffect(() => setMatchIndex(0), [query])

  const gotoMatch = (direction: 1 | -1) => {
    if (matches.length === 0) return
    const next = (matchIndex + direction + matches.length) % matches.length
    setMatchIndex(next)
    const segment = transcript.segments[matches[next]!]
    if (segment) seek(segment.startMs)
  }

  const activeSegmentIndex = useMemo(() => {
    // Falls back to segment bounds when a transcript has no word timings at all.
    const index = transcript.segments.findIndex((s) => currentMs >= s.startMs && currentMs <= s.endMs)
    return index
  }, [currentMs, transcript.segments])

  const saveSpeaker = async (speakerId: string, displayName: string | null) => {
    try {
      const response = await apiFetch(`/api/transcripts/${transcript.id}/speakers`, {
        method: 'PATCH',
        body: JSON.stringify({ speakers: [{ id: speakerId, displayName }] }),
      })
      const json = (await response.json()) as {
        ok?: boolean
        speakers?: typeof speakers
        error?: { message: string }
      }
      if (!response.ok || !json.ok || !json.speakers) {
        throw new Error(json.error?.message ?? 'Could not rename')
      }
      setSpeakers(json.speakers)
      setRenaming(null)
      toast.push({ tone: 'success', title: 'Speaker renamed', detail: 'Applied everywhere they appear.' })
    } catch (err) {
      toast.push({ tone: 'error', title: 'Could not rename the speaker', detail: (err as Error).message })
    }
  }

  /** The display name for a segment, honouring a rename made in this session. */
  const nameFor = (speakerId: string | null, fallback: string | null) => {
    if (!speakerId) return fallback
    const speaker = speakers.find((s) => s.id === speakerId)
    return speaker?.displayName ?? speaker?.label ?? fallback
  }

  // Running word index, so each rendered word knows its slot in the flat array.
  let wordCursor = -1

  return (
    <div className="space-y-5">
      {/* ── the styles the imperative highlight toggles */}
      <style>{`
        .v2b-word { border-radius: 0.25rem; padding: 0 1px; transition: background-color 90ms linear; }
        .v2b-word:hover { background-color: rgb(var(--accent-fill) / 0.18); }
        .v2b-word.${WORD_ACTIVE_CLASS} { background-color: rgb(var(--accent-fill) / 0.85); color: rgb(var(--accent-on)); }
      `}</style>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <nav className="mb-1 flex items-center gap-2 text-xs text-ink-faint" aria-label="Breadcrumb">
            <Link href="/dashboard/transcripts" className="hover:text-ink">
              Recordings
            </Link>
            <i className="bi bi-chevron-right text-[9px]" aria-hidden />
            <span className="text-ink-muted">Transcript</span>
          </nav>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{transcript.title}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-muted">
            {transcript.durationMs && (
              <span className="mono-num">
                <i className="bi bi-stopwatch mr-1.5" aria-hidden />
                {formatTimestamp(transcript.durationMs)}
              </span>
            )}
            {transcript.language && <span>{transcript.language.toUpperCase()}</span>}
            <span>{transcript.segments.length} segments</span>
            {hasWords && <span>{flatWords.length} words</span>}
            {transcript.transcribeProvider && (
              <span className="mono-num text-ink-faint">{transcript.transcribeProvider}</span>
            )}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {transcript.counts.actionItems > 0 && (
            <Link href={`/dashboard/action-items?transcriptId=${transcript.id}`}>
              <Button variant="accent" icon="bi-list-check">
                {transcript.counts.actionItems} action items
              </Button>
            </Link>
          )}
          {transcript.counts.decisions > 0 && (
            <Link href={`/dashboard/insights?transcriptId=${transcript.id}`}>
              <Button variant="secondary" icon="bi-lightbulb">
                {transcript.counts.decisions} decisions
              </Button>
            </Link>
          )}
          <div className="flex items-center gap-1">
            {(['txt', 'md', 'srt', 'vtt'] as const).map((format) => (
              <button
                key={format}
                disabled={exporting === format}
                onClick={async () => {
                  setExporting(format)
                  try {
                    const response = await apiFetch(
                      `/api/transcripts/${transcript.id}/export?format=${format}`,
                    )
                    if (!response.ok) {
                      // The export route answers 400/409 as plain text, not the JSON error
                      // envelope — it is meant for a browser navigating a download link.
                      const message = await response.text().catch(() => '')
                      throw new Error(message.trim() || 'The export failed.')
                    }
                    const blob = await response.blob()
                    const disposition = response.headers.get('content-disposition') ?? ''
                    const match = /filename="([^"]+)"/.exec(disposition)
                    const filename = match?.[1] ?? `transcript.${format}`
                    const objectUrl = URL.createObjectURL(blob)
                    const link = document.createElement('a')
                    link.href = objectUrl
                    link.download = filename
                    link.click()
                    URL.revokeObjectURL(objectUrl)
                  } catch (err) {
                    toast.push({ tone: 'error', title: 'Export failed', detail: (err as Error).message })
                  } finally {
                    setExporting(null)
                  }
                }}
                className="rounded-lg border border-edge/30 bg-surface-strong/70 px-2 py-1.5 font-mono text-[11px] uppercase text-ink-muted transition-colors hover:border-accent-edge hover:text-ink disabled:opacity-50"
                title={`Export as ${format.toUpperCase()}`}
              >
                {exporting === format ? <i className="bi bi-arrow-repeat animate-spin" aria-hidden /> : format}
              </button>
            ))}
          </div>
        </div>
      </header>

      {!transcript.diarized && (
        <p className="text-[11px] text-ink-faint">
          <i className="bi bi-info-circle mr-1.5" aria-hidden />
          Speakers were not separated — Whisper does not diarize, so segments are unattributed.
        </p>
      )}

      {/* ── player */}
      <GlassCard className="lit-edge p-4">
        {audioSrc && <audio ref={audioRef} src={audioSrc} preload="metadata" className="hidden" />}

        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            icon={playing ? 'bi-pause-fill' : 'bi-play-fill'}
            onClick={() => {
              const audio = audioRef.current
              if (!audio) return
              if (audio.paused) void audio.play().catch(() => undefined)
              else audio.pause()
            }}
          >
            {playing ? 'Pause' : 'Play'}
          </Button>

          <div className="flex items-center gap-1">
            <Button variant="ghost" icon="bi-skip-backward-fill" onClick={() => seek(Math.max(0, currentMs - 10_000), { play: playing })}>
              10s
            </Button>
            <Button variant="ghost" icon="bi-skip-forward-fill" onClick={() => seek(currentMs + 10_000, { play: playing })}>
              10s
            </Button>
          </div>

          <span className="mono-num tabular-nums text-ink-muted">
            {formatTimestamp(currentMs)} / {formatTimestamp(transcript.durationMs ?? 0)}
          </span>

          <label className="flex items-center gap-1.5 text-xs text-ink-muted">
            <span className="sr-only">Playback speed</span>
            <select
              value={rate}
              onChange={(e) => {
                const value = Number(e.target.value)
                setRate(value)
                if (audioRef.current) audioRef.current.playbackRate = value
              }}
              className="rounded-lg border border-edge/30 bg-base px-2 py-1 text-xs"
            >
              {[0.75, 1, 1.25, 1.5, 2].map((r) => (
                <option key={r} value={r}>
                  {r}×
                </option>
              ))}
            </select>
          </label>

          <Button
            variant={follow ? 'accent' : 'secondary'}
            icon={follow ? 'bi-pin-fill' : 'bi-pin'}
            onClick={() => setFollow((v) => !v)}
            className="ml-auto"
            title="Scroll the transcript to keep up with playback"
          >
            {follow ? 'Following' : 'Follow along'}
          </Button>
        </div>

        {/* progress */}
        <div className="mt-3">
          <input
            type="range"
            min={0}
            max={transcript.durationMs ?? 0}
            value={Math.min(currentMs, transcript.durationMs ?? 0)}
            onChange={(e) => seek(Number(e.target.value), { play: playing })}
            aria-label="Seek"
            className="h-1.5 w-full cursor-pointer accent-accent-fill"
          />
        </div>

        <p className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-ink-faint">
          <span>
            <kbd className="rounded border border-edge/30 px-1">space</kbd> play
          </span>
          <span>
            <kbd className="rounded border border-edge/30 px-1">←</kbd>
            <kbd className="ml-0.5 rounded border border-edge/30 px-1">→</kbd> 5s
          </span>
          <span>
            <kbd className="rounded border border-edge/30 px-1">J</kbd>
            <kbd className="ml-0.5 rounded border border-edge/30 px-1">L</kbd> 10s
          </span>
          <span>
            <kbd className="rounded border border-edge/30 px-1">F</kbd> follow
          </span>
          <span className="ml-auto">Click any word to jump there.</span>
        </p>
      </GlassCard>

      {/* ── search + speakers */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <i className="bi bi-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-ink-faint" aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                gotoMatch(e.shiftKey ? -1 : 1)
              }
            }}
            placeholder="Find in transcript…"
            aria-label="Find in transcript"
            className={clsx(inputClass, 'pl-8')}
          />
        </div>
        {query.trim().length >= 2 && (
          <div className="flex items-center gap-1.5">
            <span className="mono-num text-ink-muted">
              {matches.length === 0 ? 'no matches' : `${matchIndex + 1} of ${matches.length}`}
            </span>
            <Button variant="ghost" icon="bi-chevron-up" onClick={() => gotoMatch(-1)} disabled={matches.length === 0}>
              <span className="sr-only">Previous match</span>
            </Button>
            <Button variant="ghost" icon="bi-chevron-down" onClick={() => gotoMatch(1)} disabled={matches.length === 0}>
              <span className="sr-only">Next match</span>
            </Button>
          </div>
        )}
      </div>

      {speakers.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] uppercase tracking-wide text-ink-faint">Speakers</span>
          {speakers.map((speaker) =>
            renaming === speaker.id ? (
              <span key={speaker.id} className="flex items-center gap-1">
                <input
                  autoFocus
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void saveSpeaker(speaker.id, draftName)
                    if (e.key === 'Escape') setRenaming(null)
                  }}
                  placeholder={speaker.label}
                  className={clsx(inputClass, 'w-32 py-1 text-xs')}
                />
                <Button variant="accent" icon="bi-check-lg" onClick={() => void saveSpeaker(speaker.id, draftName)}>
                  <span className="sr-only">Save</span>
                </Button>
              </span>
            ) : (
              <button
                key={speaker.id}
                onClick={() => {
                  setRenaming(speaker.id)
                  setDraftName(speaker.displayName ?? '')
                }}
                className="inline-flex items-center gap-1.5 rounded-full border border-edge/25 bg-surface-strong/60 px-2.5 py-1 text-xs text-ink transition-colors hover:border-accent-edge"
                title={`Rename ${speaker.label}`}
              >
                <i className="bi bi-person" aria-hidden />
                {speaker.displayName ?? speaker.label}
                <i className="bi bi-pencil text-[9px] text-ink-faint" aria-hidden />
              </button>
            ),
          )}
        </div>
      )}

      {/* ── the transcript */}
      <GlassCard className="p-0">
        <div ref={scrollRef} className="max-h-[62dvh] overflow-y-auto px-4 py-4 sm:px-5">
          {transcript.segments.length === 0 ? (
            <p className="py-8 text-center text-sm text-ink-muted">
              This recording has no transcript text yet.
            </p>
          ) : (
            <ol className="space-y-4">
              {transcript.segments.map((segment, segmentIndex) => {
                const isActive = segmentIndex === activeSegmentIndex
                const isMatch = matches.includes(segmentIndex)
                const isCurrentMatch = matches[matchIndex] === segmentIndex
                const name = nameFor(segment.speakerId, segment.speakerLabel)

                return (
                  <li
                    key={segment.id}
                    className={clsx(
                      'rounded-xl border px-3 py-2.5 transition-colors',
                      isActive
                        ? 'border-accent-edge bg-accent-fill/[0.07]'
                        : isCurrentMatch
                          ? 'border-warn/50 bg-warn/[0.07]'
                          : isMatch
                            ? 'border-edge/25 bg-warn/[0.03]'
                            : 'border-transparent',
                    )}
                  >
                    <div className="mb-1 flex items-center gap-2">
                      <button
                        onClick={() => seek(segment.startMs, { play: true })}
                        className="mono-num rounded border border-edge/25 px-1.5 py-0.5 text-ink-faint transition-colors hover:border-accent-edge hover:text-accent"
                        title="Play from here"
                      >
                        {formatTimestamp(segment.startMs)}
                      </button>
                      {name && <span className="text-xs font-semibold text-accent">{name}</span>}
                    </div>

                    <p className="text-sm leading-relaxed text-ink">
                      {segment.words.length > 0 ? (
                        segment.words.map((word) => {
                          wordCursor += 1
                          const index = wordCursor
                          return (
                            <button
                              key={index}
                              data-w={index}
                              onClick={() => seek(word.startMs, { play: true })}
                              className="v2b-word text-left"
                              // A real button, so seeking works from the keyboard and is
                              // announced; `aria-current` is set imperatively as it plays.
                              title={formatTimestamp(word.startMs) ?? undefined}
                            >
                              {word.text}{' '}
                            </button>
                          )
                        })
                      ) : (
                        /* No word timings — the segment text still reads, and clicking it
                           still seeks. A transcript is useful without per-word data. */
                        <button onClick={() => seek(segment.startMs, { play: true })} className="text-left">
                          {segment.text}
                        </button>
                      )}
                    </p>
                  </li>
                )
              })}
            </ol>
          )}
        </div>
      </GlassCard>

      {transcript.summary && (
        <GlassCard className="p-4">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
            <i className="bi bi-card-text mr-2 text-accent" aria-hidden />
            Summary
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-ink-muted">{transcript.summary}</p>
        </GlassCard>
      )}
    </div>
  )
}
