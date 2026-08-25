# SPEC-010 — Ingest, Transcribe, Extract

**Status:** Accepted · **Implements:** Phase 1.1–1.5, Phase 2.1 · **Fulfils:** SPEC-000 §5
**Depends on:** SPEC-004 (vault), SPEC-001 (the contract's consumer)

## 1. Purpose

Close the loop. Until now action items arrived from the seed; this makes them arrive
from a recording. Upload audio → transcribe with word timings → extract structured
action items → land them in the dashboard that already knows what to do with them.

The output contract is **already frozen** in SPEC-000 §5 and already consumed by the
board, the guardrails, and the executor. This spec is therefore about producing that
shape faithfully, not about designing it.

## 2. Scope

In: upload, storage, transcription, speaker labels where the provider supplies them,
word timings, extraction, and the job lifecycle around all of it.

Out: the separate Python ASR service, diarization from self-hosted `pyannote`, live
call capture (SPEC-013), and semantic search (SPEC-021). Those remain planned. What
ships here runs entirely in the Node tier against hosted APIs, which is what makes it
deliverable without a second deployment.

## 3. Providers — free tiers first

Both stages resolve a key through the credential vault (SPEC-004 §3), so a user's own
key wins over the deployment's.

| Stage | Provider | Model | Free tier | Diarizes |
|---|---|---|---|---|
| Transcribe | **Groq** (default) | `whisper-large-v3-turbo` | ~28,800 audio-seconds/day | no |
| Transcribe | Deepgram | `nova-3` | $200 credit, then ~$0.26/hour | **yes** |
| Transcribe | OpenAI | `whisper-1` | paid | no |
| Extract | **Groq** (default) | `openai/gpt-oss-120b` | 14,400 requests/day | — |
| Extract | Google Gemini | `gemini-3.6-flash` | 1,500 requests/day | — |
| Extract | Cerebras | `llama-3.3-70b` | 1M tokens/day | — |
| Extract | Anthropic / OpenAI | any tool-use model | paid | — |

Groq is the default for both because one free key covers the entire pipeline. Every
extraction provider is reached through an **OpenAI-compatible chat completions** call
except Gemini, which needs its own request shape — so adding a provider is a base URL
and a model id, not an integration.

### 3.1 Diarization is a provider capability, not a service

Whisper cannot attribute speech to a speaker at all — not a configuration gap, an
architectural one. So for a long time every transcript came back unattributed and the reader
said so, while the credentials catalogue advertised Deepgram as offering "built-in
diarization" and AssemblyAI as offering "diarization, word timings". Both claims were true of
the vendors and false of this application: neither was implemented as a transcriber, so
adding either key achieved nothing. Diarization did not need the separate ASR container the
catalogue's `local_whisper` entry still describes; it needed the provider already being sold.

Deepgram has its own wire format rather than the OpenAI-compatible one — raw request body,
`Authorization: Token`, query parameters instead of form fields — so it gets its own branch,
the same shape the Gemini split takes in extraction. Three details are load-bearing:

- **`utterances=true`, not just `diarize=true`.** Word-level `speaker` integers come back
  regardless, but utterances are already grouped into speaker-contiguous runs, which is
  exactly the segment shape the reader and the `Word` table are built around. Grouping the
  words by hand would reimplement that, worse.
- **Speakers are renumbered from 1.** Deepgram counts from 0; every label in this system
  reads from 1, matching the bundled sample and the reader's rename control.
- **`diarized` is computed per response, not per provider.** A single-speaker recording
  legitimately returns one label, and claiming diarization then would be a distinction
  without a difference — the reader's "speakers were not separated" notice is the honest
  answer.

Because Groq is free it ranks first, so a user with both keys still gets Whisper. Preferring
Deepgram is expressed by disabling Groq's transcription entry — see SPEC-004 §3.1, including
why disabling a *shared* key hands its dependents their own copy first.

**No key at all is not an error.** `POST /api/transcripts` reports which stage is
unavailable and links to the credentials page, and a `sample` mode transcribes a
bundled fixture so the whole path is exercisable with nothing configured.

### 3.4 A provider failure has two audiences

Model ids are the most perishable constant in this system: providers retire them on
their own schedule, and the value in the table above will be wrong at some point without
anything in this repository changing. That makes the failure *expected*, and an expected
failure has to be legible.

Every provider error therefore splits in two.

| | Goes to | Contains | Never contains |
|---|---|---|---|
| `ExtractionError.message` | the user, and the `extractError` column | one written sentence and what to change | any part of the response body, any status code, any provider internals |
| `ExtractionError.detail` | the server log only | the response body verbatim, status, code, model | anything that survives the log line — it is not persisted or returned |

The user-facing sentence is written, not interpolated. Splicing a body into it is how a
`{"error":{"internal_trace":…}}` ends up on screen, and once one code path does it the
habit spreads, so no path does it — including the generic fallback for an unrecognised
status.

**Classification.** A model that cannot serve the request is reported by Groq as `404` +
`model_not_found`, and by Groq for a retired id as `400` + `model_decommissioned`. These
are one situation from the user's side — the configured model is unusable — and have one
fix, so both classify as `model_unavailable` and both display:

> The extraction model is unavailable. Please check the configured Groq model and try again.

`model_unavailable` is **not** auto-retryable. Repeating an identical request returns an
identical 404; the user retries after changing the configuration, which is a different
request. The UI reflects this: the "a provider is configured now, retrying will work"
hint is suppressed for this code, because the key is valid and the hint would be false.

**What a failed extraction must not cost.** Extraction is the last stage, and everything
before it succeeded. So on any extraction failure:

- the transcript row, its segments, words, speakers and stored audio are **kept**, and
  its status stays `READY` — it is readable, searchable and exportable regardless;
- action items a human already decided on are kept (§8);
- `stage` is `done` and `progress` is `100`, with the failure carried in
  `extractError` / `extractErrorCode` / `extractModel` rather than by leaving the row
  stuck mid-pipeline;
- the attempt is written to the audit log as `transcript.extraction_failed`, with the
  model, the provider, the error code, the HTTP status and
  `extractionStatus: "FAILED"`. The successful counterpart records the same fields with
  `SUCCEEDED`, so the two are comparable in one query;
- retry is offered on the transcript row itself, not only in the upload panel that
  disappears on reload.

The audit write is deliberately **outside** a transaction — there is no state change to
commit alongside it — and its own failure is caught, because an audit error must not
convert a handled extraction failure into an unhandled one that leaves the row stuck.

**Keys in logs.** `detail` is provider-controlled text, so `lib/logger` masks
key-shaped substrings in *values* as well as by field name: `gsk_…`, `sk-…`, `csk-…`,
`AIza…`, `Bearer <token>` and `key=<token>` become `[redacted]` with the prefix intact,
at every nesting depth and inside `Error.message` and stack frames. The field-name
denylist cannot help when a secret arrives inside otherwise innocuous prose.

## 4. Upload

`POST /api/transcripts` accepts `multipart/form-data`.

| Rule | Value | Why |
|---|---|---|
| Max size, audio | 25 MB | What Groq and OpenAI accept in one request; rejecting locally gives a better error than a provider 413 |
| Max size, video | 500 MB | The audio track is extracted before anything is sent, so the provider cap does not bound video length (§4.1) |
| Accepted | `mp3 m4a wav webm ogg flac` + `mp4 mov mkv avi` | Audio forwarded as-is; video demuxed first |
| Type check | magic bytes, **not** the filename | An extension is a claim by the uploader; the first bytes are evidence |

### 4.1 Video: the audio track is extracted here

The transcription providers accept `flac mp3 mp4 mpeg mpga m4a ogg wav webm` — and
**not QuickTime**. An earlier version of this spec advertised `.mov` and forwarded the
container, on the theory that the provider would strip the audio itself. It does not:
an iPhone clip or a macOS screen recording passed validation and then failed at the
provider with an opaque error. Forwarding a container and hoping is not a video upload
feature.

So FFmpeg ships, and video is demuxed before anything leaves the machine: the audio
track is extracted, downmixed to mono at **16 kHz** — Whisper's own working sample
rate, so nothing usable is discarded — and encoded to MP3 at 48 kbps. Three things
follow:

1. **MOV, MKV and AVI genuinely work**, along with anything else FFmpeg can read.
2. **The provider's 25 MB cap stops bounding video length.** A screen recording is
   almost entirely pixels; in practice extraction removes about 75% of the bytes, and
   far more for longer captures.
3. **Only the audio is transmitted.** The picture never leaves the machine, which is a
   meaningful privacy property for a screen recording and is stated in the UI.

The cost is roughly 30 MB in the image. Audio already in an accepted format and under
the cap is **passed through untouched** — re-encoding good audio would only lose
fidelity and spend CPU.

Failure modes are specific rather than generic: a video with no audio track says so, a
recording still over the cap after compression says how long it is and suggests
splitting it, and a deployment without FFmpeg says that audio still works and video
does not. `GET /api/health` reports `videoUploadSupported` so this is visible before
someone tries.

Bytes are held in `TranscriptAsset` as a `Bytes` column rather than on a filesystem
or in object storage. For files this size it keeps the deployment to one stateful
service, makes the upload transactional with its `Transcript` row, and means a
`docker compose down -v` really does erase everything. Object storage is the obvious
swap at scale and is isolated behind `server/ingest/storage.ts`.

## 5. The job lifecycle

`Transcript.status` is the state machine, and the model already has it:

```
QUEUED ──▶ PROCESSING ──▶ READY
   │            │
   └────────────┴────────▶ FAILED   (with a reason the user can act on)
```

Processing runs **after the response**, not inside it: transcription of an hour of
audio takes longer than any sensible request timeout. The upload returns `202` with
the transcript id, and the client polls `GET /api/transcripts/:id`.

Progress is a stage plus a percentage, stored on the row so a reload does not lose
it: `uploaded → transcribing → extracting → done`. Each stage records its own error,
so a failure says *which half* broke — a transcript that succeeded but whose
extraction failed keeps the transcript and can be re-extracted alone.

**Idempotency**: the sha-256 of the uploaded bytes is stored. Re-uploading identical
audio returns the existing transcript instead of spending the free tier twice.

## 6. Transcription

One provider interface, `TranscriptionProvider`, mirroring the integration registry:
`isConfigured()`, `transcribe(bytes, filename, options)`, returning

```jsonc
{
  "language": "en",
  "durationMs": 1320000,
  "segments": [
    { "startMs": 61000, "endMs": 65200, "text": "…", "speakerLabel": "Speaker 1",
      "words": [{ "text": "Let's", "startMs": 61000, "endMs": 61240, "confidence": 0.97 }] }
  ]
}
```

`verbose_json` with `timestamp_granularities: ["word", "segment"]` gives word timings
from Whisper. Words are persisted to the `Word` table the player already expects.

**Speaker labels are best-effort and honestly reported.** Whisper does not diarize.
Where a provider returns speakers we use them; where it does not, segments carry a
null speaker and the transcript is flagged `diarized: false` so the UI says "speakers
not separated" rather than implying a single speaker — which stays true for Whisper, since
it cannot diarize at all. Diarization arrives by choosing a provider that can (§3.1), not
by adding a service.

Audio longer than the provider's per-request limit is **chunked on segment
boundaries** with the offsets re-based, so a 90-minute recording is not simply
refused.

## 7. Extraction

A single tool-use call per transcript, with the schema from SPEC-000 §5 as the tool
definition — so the model fills a shape rather than being asked for JSON and trusted
to comply. The response is validated with zod; anything failing validation is
dropped with a logged reason rather than persisted half-formed.

The prompt carries the transcript **with timestamps inline**, because the contract
requires every action to cite one. Rules given to the model, each present because its
absence produces a specific failure:

1. **Only what was actually committed to.** Hypotheticals, questions, and things
   someone declined are not action items. This is the difference between useful and
   noise.
2. **Quote verbatim.** `sourceQuote` must be a substring of the transcript, and is
   **verified** to be one after the fact. A paraphrase defeats the entire grounding
   mechanism, so a failed check downgrades confidence to `LOW` rather than trusting it.
3. **Leave it null rather than inventing it.** No owner, no deadline, no payload
   field may be guessed. The dashboard is built to show "needs: startsAt" — a
   plausible fabrication is far worse than a blank.
4. **Report low confidence.** Hedged language ("someone should probably…") must come
   back `LOW`, which keeps it out of the ready lane by construction.
5. **Mark superseded actions.** When a later moment revises an earlier one, both are
   returned and the earlier points at the later — the data the dashboard already
   renders and `POL_SUPERSEDED` already blocks on.
6. **Deadlines resolve against a stated "now"**, passed in the prompt. "By Friday"
   is meaningless without it, and a model left to guess the date will guess.

Decisions are extracted in the same call, since `POL_CONTRADICTS_DECISION` already
consumes them and a second pass would double the cost for nothing.

Payloads are **derived, not asked for**: given a type, an owner and a deadline, the
provider-shaped payload is assembled in code. Asking a model to emit
`durationMinutes` and `attendees[]` invites it to invent attendees.

## 8. Re-extraction

`POST /api/transcripts/:id/extract` re-runs extraction on an existing transcript.
Existing action items that a human has **already decided on** (anything not
`PROPOSED`) are left alone; only untouched proposals are replaced. Discarding a
reviewer's decisions because a model was re-run would be indefensible.

It is the recovery path for §3.4 and is offered wherever a failure is shown: the upload
panel, and the transcript row itself. Both read the message through one helper, because
this route answers with a service result on a handled failure (`error` is a string) and
with `{error:{code,message}}` on a throw — client code that reads one shape shows nothing
for the other.

## 9. API

| Route | Purpose |
|---|---|
| `POST /api/transcripts` | multipart upload; `202` + id, or `200` + existing id when identical |
| `GET /api/transcripts` | list with status and counts |
| `GET /api/transcripts/:id` | one transcript, its progress, and its segments |
| `DELETE /api/transcripts/:id` | remove it and everything cascading from it |
| `POST /api/transcripts/:id/extract` | re-extract (§8) |
| `GET /api/transcripts/:id/audio` | stream the stored bytes for the player |
| `GET /api/pipeline/status` | which stages are configured, and via which provider |

## 10. Acceptance criteria

0. A Groq `404` / `model_not_found` shows the §3.4 sentence and nothing from the
   response body, keeps the transcript and its segments, writes
   `transcript.extraction_failed` with the model and status, and leaves a working retry
   on the row.
1. With a Groq key, a real recording produces a transcript with word timings and
   action items visible on the board.
2. With no keys, `sample` mode still produces both, and the API names the missing
   stage rather than failing opaquely.
3. A file whose magic bytes do not match an accepted type is refused, whatever its
   extension says.
4. A file over 25 MB is refused locally, before any provider call.
5. Re-uploading identical bytes returns the existing transcript and spends no quota.
6. Every extracted `sourceQuote` is verified against the transcript; a failure
   downgrades confidence rather than being trusted.
7. No action item is created with an invented owner or deadline.
8. A transcription failure leaves `FAILED` with a reason; an extraction failure keeps
   the transcript and can be retried alone.
9. Re-extraction preserves every action item a human has decided on.
10. Extracted items land in the correct readiness group and are subject to the same
    guardrails as seeded ones.
