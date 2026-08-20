# SPEC-014 — Voice to BRD

*The flagship surface. Speak a requirement; get a business requirements document; speak
again to refine it.*

## 1. Purpose

The tool is named Vowcraft, and until now its landing page was an analytics overview —
the thing you look at *after* doing work, presented as the thing you do first. This spec
makes the primary act primary: sign in, press one button, talk, and watch a BRD assemble
itself from what you said.

Two properties distinguish this from the upload pipeline (SPEC-010):

- **It is live.** Transcription appears while you speak, not after a file finishes
  uploading. That needs a streaming ASR provider, which none of the existing
  file-upload providers are (§3). This is the streaming-ASR capability ROADMAP 1.9
  described; SPEC-013 (live Teams/Meet capture) remains a separate, unbuilt feature that
  will consume the same transport.
- **It is incremental.** Speaking more requirements *revises* the existing document
  rather than regenerating it. A user who adds one sentence about audit logging must not
  lose the six sections they already accepted (§6).

## 2. The interaction

One screen, at `/dashboard`, in four states. There is deliberately **no text input** —
this is a voice surface, and offering a textarea invites people to type, at which point
the streaming ASR is dead weight and the product is a worse ChatGPT.

| State | What the user sees |
|---|---|
| `idle` | A single large record button, centred. Nothing else competes with it. |
| `recording` | The button shrinks and moves to the top-centre, becoming a stop control. Live transcription fills the space below it, growing downward. |
| `generating` | Transcript freezes; a progress affordance sits beneath it. |
| `ready` | The BRD renders below the transcript. A smaller "add requirements" record button appears, which begins a *revision* rather than a new document. |

The button's travel is the state indicator. A user who has just pressed record sees the
control they pressed move and change shape, so there is never a question of whether the
recording started.

### 2.1 Interruption is not an error

Closing the tab mid-recording, losing the network, or a provider dropping the socket
must not lose what was already transcribed. Every finalised transcript segment is
persisted as it arrives (§4.3), so the worst case is a truncated requirement, not an
empty screen. On reconnect the session resumes from what is stored.

## 3. Streaming transcription

**Provider: Deepgram** (`nova-3` over WebSocket), already in the credential catalogue
(SPEC-004 §6) as a `TRANSCRIPTION` service. The file-upload providers used by SPEC-010 —
Groq Whisper, OpenAI, Gemini — are batch APIs with no streaming endpoint, so this stage
cannot reuse them.

### 3.1 The browser connects to Deepgram directly, with a key it cannot keep

The obvious design — browser → our server → Deepgram — needs a long-lived WebSocket on
our side, which Next.js route handlers do not provide and Vercel's serverless runtime
does not host. The obvious alternative — ship `DEEPGRAM_API_KEY` to the browser — hands
every user a credential with full project scope.

Neither. `POST /api/speech/token` mints a **short-lived, minimally-scoped Deepgram key**
server-side and returns only that:

| Property | Value | Why |
|---|---|---|
| TTL | 60 seconds | Long enough to open a socket, useless if captured from a log |
| Scope | `usage:write` only | Cannot read the project, list keys, or see billing |
| Minting | server-side, from the vault-resolved key | The real key never reaches the browser |

The long-lived key stays where every other credential stays: encrypted in the vault,
resolved per user (SPEC-004 §3). A user with their own Deepgram key uses it; otherwise
the deployment's `DEEPGRAM_API_KEY` applies.

### 3.2 No key configured is a stated condition, not a silent degradation

If neither tier resolves a Deepgram key, the record button is **disabled with the reason
on it** and a link to Settings → API keys. It does not fall back to a lower-quality
provider, and it does not present a working-looking button that fails on click. This
follows the same rule as SPEC-010 §3: an unconfigured stage says so.

There is no mock mode for this surface. A simulated live transcript would be
indistinguishable from a real one on screen, which is exactly the confusion the rest of
this codebase refuses to create.

## 4. Data model

| Model | Purpose |
|---|---|
| `BrdDocument` | One BRD. Holds the current rendered content and its title. |
| `BrdRevision` | One speaking turn: what was said, what changed, and the resulting content. Ordered by `ordinal`. |

`BrdDocument.content` is the *current* document, denormalised so listing and reading
never replay revisions. `BrdRevision.content` is the full document as of that revision,
so any point in history is readable without reconstruction — revisions are an audit
trail, not a diff chain that breaks if one link is malformed.

### 4.1 Why the spoken text lives on the revision

`BrdRevision.spokenText` is the transcript of that turn alone, not the accumulated
conversation. It answers "what did I say that caused this change?", which is the
question a user asks when a section looks wrong. The accumulated text is derivable by
concatenation; the per-turn text is not recoverable if only the total is stored.

### 4.2 Content shape

`content` is a JSON object matching §5's schema, not a markdown string. Markdown is a
*rendering* of the document; storing it as prose would mean the refinement step has to
parse its own previous output back into structure, and any formatting drift becomes a
data-integrity problem. Markdown is generated on export (§8).

### 4.3 Partial sessions

`BrdDocument.status` is `DRAFTING` while a recording is open and `READY` once a
generation completes. A `DRAFTING` document with revisions but no content is the
persisted form of "the user was interrupted" (§2.1) and is resumable, not garbage.

## 5. The BRD structure

A forced tool call against the extraction provider layer (SPEC-010 §3), reusing its
provider resolution, its error classification (§3.4), and its model catalogue. The tool
schema:

| Field | Type | Notes |
|---|---|---|
| `title` | string | Derived from the requirement, not "Untitled BRD" |
| `executiveSummary` | string | 2–4 sentences |
| `objectives` | string[] | What success is |
| `scope.inScope` / `scope.outOfScope` | string[] | Both, because omitting out-of-scope is how scope creeps |
| `stakeholders` | `{ role, interest }[]` | Roles, not invented names |
| `functionalRequirements` | `{ id, requirement, priority, rationale }[]` | `id` is `FR-01`-style, stable across revisions |
| `nonFunctionalRequirements` | `{ category, requirement }[]` | Performance, security, accessibility, etc. |
| `assumptions` / `risks` | string[] | Risks carry a mitigation where one was stated |
| `openQuestions` | string[] | **Required to be non-empty when the input was thin** |

### 5.1 Open questions are the honesty mechanism

A five-second spoken sentence cannot specify a system, and a model asked for a BRD will
produce a confident-looking one anyway — inventing stakeholders, SLAs and compliance
requirements nobody mentioned. `openQuestions` is where the model is required to put
what it does not know, and the prompt (§7) instructs it to prefer an open question over
an invented requirement. A BRD with twelve requirements and no open questions from one
sentence of input is a failure of this spec, not a success.

## 6. Incremental refinement

`POST /api/brd/:id/refine` takes newly spoken text and the existing document, and returns
the *revised* document. The prompt is given the current content as structured JSON and
instructed to:

- **preserve** every existing requirement id and its wording unless the new input
  contradicts or extends it;
- **append** new requirements with fresh ids continuing the sequence;
- **resolve** open questions the new input answers, moving them into the body;
- **report** what it changed, in `changeSummary`, per revision.

`changeSummary` is stored and displayed. Without it, refinement is a black box — the
document silently differs and the user has to diff two versions by eye to find out what
their sentence did.

### 6.1 Requirement ids are stable

`FR-03` must refer to the same requirement in revision 5 as in revision 1. Renumbering
on every pass would make the change summary meaningless and any external reference to a
requirement id wrong. The prompt states this; §9 tests it.

## 7. Prompts

Two prompts, both in `server/brd/prompt.ts`:

- **initial** — structure-from-nothing, with §5.1's instruction to prefer open questions
  over invention.
- **refine** — receives current content plus new spoken text, with §6's preserve/append/
  resolve rules and an explicit instruction that omitting an existing requirement is an
  error, not a summarisation.

Both state that the transcript is speech: it contains false starts, repetition and
filler, and the document should reflect the *intent*, not transcribe the delivery.

## 8. Routes

| Route | Purpose |
|---|---|
| `POST /api/speech/token` | Mint a 60-second Deepgram key (§3.1) |
| `GET /api/speech/status` | Whether streaming ASR is configured, and via which tier |
| `POST /api/brd` | Create from the first turn's spoken text |
| `GET /api/brd` | List the user's documents |
| `GET /api/brd/:id` | One document with its revisions |
| `POST /api/brd/:id/refine` | Incremental revision from new spoken text (§6) |
| `PATCH /api/brd/:id` | Rename |
| `DELETE /api/brd/:id` | Remove it and its revisions |
| `GET /api/brd/:id/export?format=md\|json` | Download |

## 9. Acceptance criteria

1. With no Deepgram key, the record button is disabled and states why; no fake transcript
   appears.
2. `POST /api/speech/token` returns a key that is not the configured key, and expires.
3. Speaking a requirement produces a document whose `title` reflects the content.
4. A thin input (one sentence) yields `openQuestions`, not a fully-specified invention.
5. Refinement preserves every existing `FR-` id and its wording where uncontradicted.
6. Refinement appends new requirements with continuing ids.
7. Each revision stores the spoken text for that turn alone and a non-empty
   `changeSummary`.
8. The history module lists documents and opens any revision.
9. A document interrupted mid-recording is resumable rather than lost or blank.
10. Markdown export contains every section that has content.
11. Every audit-relevant action (`brd.created`, `brd.revised`, `brd.deleted`) is recorded.
12. The record surface is operable by keyboard, and the live region announces state
    changes to a screen reader.
