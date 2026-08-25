# SPEC-012 — Transcript Reader

**Status:** Accepted · **Implements:** Phase 1.7 · **Depends on:** SPEC-010 (produces the data)

## 1. Purpose

Hear what was said. The ingest pipeline already persists word-level timings and serves
the audio with range requests, so this is the reading surface over data that exists —
not new capability.

Its most important job is not playback. It is **closing the grounding loop**: an
action item cites a timestamp and a quote, and until now a reviewer had to take both on
trust. Clicking that timestamp should play the sentence.

## 2. Routes

| Route | Purpose |
|---|---|
| `/dashboard/transcripts/:id` | The reader itself. `?t=<ms>` opens at a moment, `?q=<term>` opens with a search applied. Both are what the deep links from elsewhere use. |
| `/dashboard/transcripts/reader` | The module's landing route. Redirects to the most recently created transcript that has segments; explains itself and links to Recordings when there is none. |

### 2.1 Why the reader needs a landing route of its own

Reading only makes sense for a *specific* transcript, so the reader has no natural
standalone page — which is why it originally shared `/dashboard/transcripts` with
Recordings in the nav. That made both nav entries highlight simultaneously on every
transcripts URL, since the active check matches a route and its descendants and the two
entries were the same route.

The two are genuinely different features (SPEC-010 is upload and the library; this spec is
playback and grounding), so they get different routes rather than a special case in the
nav. The division of the shared prefix:

- **Recordings** owns exactly `/dashboard/transcripts` — the bare list, and nothing under it.
- **Transcript reader** owns everything *under* `/dashboard/transcripts/`, which is both
  `/reader` and any `:id`. So opening a transcript from the library, or following an action
  item's citation, highlights the reader rather than leaving the nav pointing at the list
  the user has navigated away from.

## 3. Playback and highlighting

- `<audio>` against `GET /api/transcripts/:id/audio`, which already honours `Range`, so
  seeking does not re-download the file.
- The current word is highlighted as it is spoken; the current segment is tinted.
- Clicking any word seeks to it. That is the primary interaction — more people will use
  it than the play button.

### 3.0 When there is no audio at all

Not every transcript has playable audio, so the player is conditional on the API's
`hasAudio` flag rather than always rendered:

- **A live capture never stores audio** (SPEC-013). The shared tab's stream is transcribed
  as it happens and discarded; only segments are persisted. There is nothing to play, and
  that is by design rather than a failure.
- The reader previously fetched the audio regardless and reported *"Could not load the
  audio"* — presenting an entirely expected condition as an error the user had to dismiss.
  It now skips the request and replaces the player with a sentence explaining why, wording
  it differently for a live capture (never recorded) than for an upload whose asset is gone.
- **`hasAudio` is computed from the stored asset, not inferred from `sourceType`.** The two
  can disagree: an upload whose asset was pruned is equally unplayable. The reader needs to
  know whether a player can work, not how the words arrived.

Everything else on the page — the transcript, search, speaker renaming, export, and deep
links from an action item's citation — works unchanged without audio. Word-level
highlighting is the only feature that depends on playback.

### 3.1 Why the highlight is imperative

A 90-minute meeting is roughly 15,000 words, and `timeupdate` fires about four times a
second. Re-rendering 15,000 React nodes 4×/s is not viable, and memoising each word
still costs a reconciliation pass over the whole list.

So the active word is found by **binary search over a flat, pre-sorted array** (O(log n))
and applied by **toggling a class on two DOM nodes** — the one leaving and the one
entering. React owns the structure; the ticking highlight is imperative because that is
what it takes to stay smooth at that scale.

### 3.2 Follow mode

The active word scrolls into view, but **only while the reader has not scrolled
manually**. Auto-scroll that fights the user is the single most irritating thing a
player like this can do. Any manual scroll suspends following and shows a "follow
along" control to resume; seeking re-enables it, because a seek is an explicit
statement about where attention should be.

## 4. Speakers

Whisper does not diarize, so segments arrive unattributed and the reader says so rather
than implying one speaker (SPEC-010 §6). Where labels *do* exist — the bundled sample,
or a provider that supplies them — they can be **renamed**: `Speaker 1` → `Marcus`, once,
applied everywhere the label appears.

Renaming writes `Speaker.displayName` and leaves `label` untouched, so the machine's
original attribution is never lost and a re-transcription can still be matched up.

## 5. Search

Substring search over segment text, case-insensitive, with match count, next/previous
navigation, and every hit marked. Selecting a hit seeks to that segment.

Not semantic search — that is SPEC-021 and needs embeddings. Saying "search" and
delivering substring matching is fine as long as the UI does not imply otherwise.

## 6. Export

| Format | Route | Notes |
|---|---|---|
| `txt` | `GET /api/transcripts/:id/export?format=txt` | Timestamped, speaker-prefixed |
| `md` | `…?format=md` | Same, with a heading and metadata |
| `srt` | `…?format=srt` | Subtitles, from the real segment timings |
| `vtt` | `…?format=vtt` | WebVTT, for a browser `<track>` |

SRT and VTT are genuinely correct rather than approximated, because the timings are
real. **PDF and DOCX are deliberately absent**: both need a rendering dependency, and a
badly generated PDF is worse than a text file the user can convert. Listed as not
included rather than quietly missing.

## 7. Deep links in

The grounding loop, made real:

- An action item's `⏱ 12:34` links to `/dashboard/transcripts/:id?t=754000`.
- A decision's citation does the same.
- The reader opens paused at that moment with the segment highlighted, so the reviewer
  reads the line and can press play to hear it.

## 8. Accessibility

- Words are `<button>` elements, so seeking works from the keyboard and is announced.
- Space toggles play/pause, `←`/`→` seek 5s, `J`/`L` seek 10s, `K` toggles, `F` toggles
  follow — all disabled while a text input has focus.
- The transcript remains fully readable and selectable with audio never played; the
  player is an enhancement, not a requirement.
- `aria-current="true"` marks the spoken word for a screen reader.

## 9. Acceptance criteria

1. A transcript with word timings renders every word, and the count matches the database.
2. Clicking a word seeks the audio to that word's start.
3. The highlight advances during playback without re-rendering the word list.
4. Manual scrolling suspends follow mode; seeking resumes it.
5. `?t=` opens at that moment with the containing segment highlighted.
6. Renaming a speaker updates every segment attributed to that label and persists.
7. Search reports a match count and can step through hits.
8. SRT export timings match the stored segment boundaries.
9. A transcript with no words still renders its segment text rather than an empty page.
10. Every control is reachable and operable by keyboard.
11. On `/dashboard/transcripts` exactly one nav entry is marked current, and it is
    Recordings; on `/dashboard/transcripts/:id` exactly one is, and it is Transcript
    reader (§2.1).
12. `/dashboard/transcripts/reader` redirects to a readable transcript when one exists,
    and renders an explanation with a route to Recordings when none does.
