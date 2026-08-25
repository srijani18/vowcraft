# SPEC-013 — Live meeting capture

**Status:** Accepted · **Implements:** Phase 1.8
**Depends on:** SPEC-014 §3 (the streaming relay this reuses unmodified), SPEC-010 (the `Transcript`/`Segment` model this lands in)

## 1. Why tab-audio capture, not a bot

The original shape of this feature was a bot joining a Google Meet call directly, via
Google's Meet Media API — real-time WebRTC audio, no browser involved. That API exists,
but access is gated behind Google's **Workspace Developer Preview Program**, and per
Google's own documentation the gate applies not just to the calling application but to
**every participant in every conference** the bot would join. That makes it unworkable
for a product feature serving arbitrary customer meetings today, regardless of engineering
effort — it is a policy gate, not a build problem, and there is no published timeline for
it to open up.

The substitute shipped here is **vendor-agnostic browser tab-audio capture**:
`getDisplayMedia({video:true, audio:true})` while the user is already in a call, in their
own browser tab. No OAuth app, no vendor SDK, no bot infrastructure, no participant-side
enrollment — and it works identically whether the shared tab has Meet, Teams, Zoom, or
anything else open, since the browser is capturing the tab's *audio output*, not
integrating with any specific meeting tool.

## 2. Scope

**In:** capturing a shared tab's audio, streaming it live through the existing
SPEC-014 §3 relay, and finalizing the result into an ordinary `Transcript` with `Segment`
rows once the user stops — landing on the same dashboard, reader, and action-item pipeline
every uploaded recording already uses.

**In, provider permitting:**
- **Diarization.** Originally out, on the reasoning that a single mixed stream has no
  speaker-separation signal to work with. That was wrong about the provider, not about the
  audio: Deepgram diarizes on its *streaming* endpoint too, so the signal was available and
  simply never requested. The relay now asks for it and forwards the index Deepgram reports.
  The earlier refusal to invent a "Speaker 1" still stands — a provider that does not diarize
  yields no labels rather than a fabricated one, and `diarized` is computed from what
  actually arrived. See §4.1.

**Out, deliberately:**
- **Word-level timing.** The live relay's frames (`Utterance(text, is_final)`,
  `app/services/speech.py`) carry no timing at all, unlike an upload's provider response.
  Segment boundaries are synthesized from each utterance's client-reported arrival time —
  accurate to "when it arrived," not to the word.
- **Video, in any form.** The shared tab's video track is stopped and dropped
  client-side the moment the share starts; nothing server-side ever receives it.
- **Automatic start/stop.** No voice-activity detection, no auto-join. The user shares a
  tab and clicks a button (or later, uses the browser's own "Stop sharing" control) —
  matching the "a human disposes" posture the rest of this app already applies everywhere.
- **Any vendor-specific integration.** This does not join, authenticate against, or know
  anything about Meet, Teams, or Zoom specifically — it only ever sees a tab's audio.

## 3. Capture mechanism

`getDisplayMedia({video: true, audio: true})` — video is requested alongside audio because
some Chrome versions do not reliably surface the tab-sharing picker for an audio-only
request. The returned stream's video track(s) are stopped immediately
(`track.stop()`) and never rendered; a new `MediaStream` built from only the audio
track(s) is what actually feeds `MediaRecorder`, identical in every other respect to how
`useLiveTranscription.ts` (SPEC-14 §3's existing mic-capture hook) already uses
`MediaRecorder`.

Two failure modes specific to tab sharing, handled explicitly rather than surfacing as a
generic error:
- **No audio track at all** — Chrome's "Share tab audio" checkbox is unchecked by
  default, and some share sources (e.g. "Entire Screen" on some OSes) never offer audio.
  Checked immediately after the picker resolves; reported as "That share had no audio —
  try again and check 'Share tab audio'," not a generic capture failure.
- **The native "Stop sharing" control** — the browser's own UI for ending a screen/tab
  share fires `track.onended` on the audio track. This is treated as an equal, first-class
  stop signal alongside an in-app "Stop and process" button, since relying solely on an
  in-app control would miss the far more discoverable native one.

## 4. Data flow: from utterances to a Transcript

**Reused as-is, unmodified**: the streaming relay (`GET /api/speech/status`,
`WS /api/speech/stream`), its bearer-subprotocol auth, its wire protocol, and
`DeepgramProvider`. Audio bytes are audio bytes regardless of source — nothing about the
relay needed to change.

**New**: `POST /api/transcripts/live` (`apps/api/app/api/routes/ingest.py`), taking the
browser's accumulated final utterances (`{text, atMs}[]`) plus a total duration. It calls
`finalize_live_capture()` (`apps/api/app/services/live_capture.py`):

1. `_segments_from_utterances()` — a pure function, sorts utterances by `atMs`, clamps to
   a strictly increasing sequence (so an out-of-order or duplicate client timestamp can
   never produce a zero-or-negative-length segment), and shapes them into the exact
   `{"startMs", "endMs", "text", "speakerLabel": None, "words": []}` dicts
   `transcription.py`'s providers already produce for an upload.
2. Creates a `Transcript` row with `source_type="TAB_CAPTURE"` (a new enum value,
   deliberately distinct from `MEET`/`TEAMS`, which stay reserved for a possible future
   vendor-bot integration — reusing either would misrepresent a Zoom or generic tab
   capture as a specific vendor's own), `diarized=False`, no `TranscriptAsset` (there is
   no uploaded file — `Transcript.asset` is already `Optional`, and
   `TranscriptService.audio()` already 404s gracefully rather than crashing when one is
   absent, so this is a pre-existing supported shape, not a new failure mode).
3. Persists segments via `persist_segments()` — extracted verbatim out of the upload
   pipeline (`apps/api/app/services/ingest/segments.py`) so both paths write
   `Speaker`/`Segment`/`Word` rows through identical code, never two implementations that
   could drift.
4. Fires `run_extraction_and_embedding()` — also extracted out of the upload pipeline
   (now a named function in `apps/api/app/services/ingest/pipeline.py`, called by both the
   full upload pipeline and this route) — which calls `extract_into()` and
   `embed_segments_into()` completely unmodified. Neither function has any dependency on
   there having been an uploaded file; both operate purely on `transcript_id` and
   already-persisted `Segment` rows.

The response lands on the **existing** `/dashboard/transcripts/{id}` page — no new
frontend surface for viewing results was needed, because the result is an ordinary
`Transcript` row the reader, the action-items board, and the audit log already know how
to render.

### 4.1 How a speaker reaches a segment

Diarization on this path is a value threaded through seven layers, and every one of them
had to change — which is why it stayed unimplemented long after the provider supported it:

1. `DeepgramProvider._url` requests `diarize=true` on the socket.
2. The frame's speaker sits on the **words**, not the alternative, so the first word
   carrying one answers for the utterance (a frame is speaker-contiguous in practice).
3. `Utterance` gains `speaker: Optional[int]` — the raw provider index, not a label.
4. The WebSocket frame gains `"speaker": int|null`, extending the client contract.
5. `TranscriptAccumulator` retains it per final utterance; it is the only thing holding
   this between the socket and the POST.
6. `POST /api/transcripts/live` accepts it per utterance, bounded (`0..99`) because it
   arrives from a browser — a wild value would become "Speaker 4000000".
7. `_segments_from_utterances` maps it to `Speaker N`, numbering from 1.

Two things are deliberate. **The raw index travels, not a `"Speaker 1"` string**, so
labelling is one presentation decision made where segments are built rather than three
along the way. And **`speaker` stays optional rather than defaulting to 0**, because 0 is a
real speaker index — interim frames sometimes omit it, and a default would attribute
everything to the first speaker.

There is no audio to fall back on and no word timings, so **segment labels are the only
place a live capture's speakers exist**. Getting the off-by-one wrong here would not degrade
the feature, it would misattribute sentences to the wrong person while presenting them as
fact — which is why both the numbering and the "index 0 is not absent" case are pinned by
tests on both sides of the wire.

## 5. Known v1 limitations

Diarization only when the provider supports it (Deepgram does; a self-hosted WhisperLive
does not); approximate (not word-level) timing; no video capture or storage; manual
start required and manual-or-native stop; vendor-agnostic only (no "joins the call"
capability for any specific tool); requires Chrome or Edge (`getDisplayMedia` audio-track
support is inconsistent on Safari/Firefox, checked and surfaced explicitly rather than
attempted silently).

## 6. Testing

Following this repo's actual convention: service-layer Postgres integration tests, no
route-level tests.

- `apps/api/tests/test_live_capture_service.py` — `_segments_from_utterances()` as a pure
  function (single utterance, chained timestamps, out-of-order input, duplicate/
  non-increasing timestamps never producing a zero-length segment, blank utterances
  dropped); `finalize_live_capture()` end-to-end (creates a `TAB_CAPTURE` transcript,
  persists matching segments, creates zero `Speaker` rows, rejects an empty utterance list
  without creating anything, invokes extraction/embedding for the new transcript, title
  generation when none is supplied).
- `apps/api/tests/test_ingest_segments.py` — regression pin that `persist_segments()`,
  extracted out of the upload pipeline, produces identical rows to what the pre-extraction
  inline block produced, using the bundled sample fixture's own output as input.
- `tests/speech-client.test.ts` — `TranscriptAccumulator`'s new `atMs`/`utterances`
  support (final-with-timestamp is recorded, interim frames never appear, a final with no
  `atMs` still commits to `finalText` but not to `utterances`, `reset()` clears both).
- Explicitly manual/browser-only, not unit-testable: the actual `getDisplayMedia` picker
  UX and its "Share tab audio" checkbox behavior, whether stopping the video track early
  affects Chrome's sharing indicator while audio capture continues, `track.onended`
  firing reliably across Chrome/Edge, and an end-to-end capture producing a real
  transcript with extracted action items once a credential is configured.
