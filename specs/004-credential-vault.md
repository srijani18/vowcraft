# SPEC-004 — Credential Vault (bring your own keys)

**Status:** Accepted · **Depends on:** SPEC-000, SPEC-002 §4

## 1. Problem

The system needs third-party keys for transcription, extraction, translation,
embeddings, and non-OAuth integrations. Requiring them in `.env` at deploy time
fails two real cases: a self-hoster who wants to try the app before choosing a
provider, and a user on a shared deployment who wants to spend their own quota.

## 2. Goal

If a key is absent from the environment, the user can supply it from
`/dashboard/settings/credentials`, encrypted at rest, per user, per service —
and see exactly which module each key unlocks and whether it currently works.

## 3. Resolution order

`resolveCredential(userId, service)` returns the first hit:

1. **User credential** — a `Credential` row for that user and service, `enabled`,
   status not `INVALID`, **and whose ciphertext actually decrypts**. Source `USER`.
   The decryption check is not paranoia: after an `APP_ENCRYPTION_KEY` rotation the
   row still exists and still looks valid, and reporting it as configured would be
   a lie the UI and the executor both act on. An undecryptable row is surfaced as
   `INVALID` with "re-enter the key" and the lookup falls through to the next tier.
2. **Environment** — the mapped `process.env` var. Source `ENV`.
3. **Absent** — source `NONE`; the caller degrades (see §7).

A user key wins over the environment on purpose: on a shared deployment "use my
own quota" must be expressible, and the operator's fallback key stays the
default for everyone who hasn't set one. The active source is always shown in
the UI next to the field, so precedence is never a surprise. Set
`CREDENTIALS_ENV_LOCKED=true` to invert this and pin the environment as
authoritative for a locked-down deployment.

### 3.1 Shared keys, and disabling one

Several providers cover more than one capability from a single account: one Groq key serves
both `/audio/transcriptions` and `/chat/completions`, one OpenAI key serves audio, chat and
embeddings. These are separate catalogue entries — genuinely different capabilities with
different allowances — but a user who pastes the key once must not then be told the *other*
module is unconfigured. `sharesKeyWith` links them, and resolution falls through to the
sibling as a fourth tier, after the environment.

`enabled` is what expresses **provider preference**. Resolution walks the catalogue in
order, so turning one entry off promotes the next — and that is the only way to prefer a
provider ranked below a configured one. Deepgram is the case that needs it: it is the only
transcriber that diarizes, but Groq is free and therefore listed first, so "give me speaker
labels" is said by switching Groq's transcription entry off. Toggling is a `PATCH`, not the
`PUT` that saves a key: there is no read path for a stored secret, so flipping a flag must
not mean pasting the key again.

**Disabling a shared key hands each dependent its own copy first.** This is the subtle part,
and it shipped wrong. `enabled` is a column on the *row*, and one row serves both entries —
so disabling "Groq — Whisper" also disabled "Groq — Llama / Qwen" and silently broke
action-item extraction. Someone following the documented route to diarization lost extraction
as a side effect, with nothing to connect cause to effect. The convenience of not pasting
twice had become a trap.

So before the flag flips, the secret is copied into the row of every dependent that has none
of its own (and that is not satisfied from the environment, which needs no copy). The
dependent then resolves from its own row — checked *before* the sibling fallback — and the
two are independent from that point on, so re-enabling the original does not re-couple them.
An existing dependent row is never overwritten: it is already independent, and replacing it
would discard a deliberate choice. The response reports what was copied and the UI says so,
because duplicating a user's secret on their behalf is not something to discover later.

## 4. Encryption at rest

- One `Credential` row stores **all fields for one service** as a single
  AES-256-GCM ciphertext over a JSON object: `{"apiKey":"sk-…"}`,
  `{"clientId":"…","clientSecret":"…"}`.
- Key: `APP_ENCRYPTION_KEY`, 32 random bytes, base64. Same primitive as OAuth
  tokens (`lib/crypto.ts`) — one audited implementation, not two.
- Ciphertext format `v1.<iv>.<tag>.<ct>`, base64url, with an explicit version
  prefix so a future key rotation or algorithm change is decidable per row.
- The AAD binds each ciphertext to `userId:service`, so a row copied to another
  user or service fails to decrypt rather than silently working.

## 5. What may leave the server

Plaintext secrets **never** leave the process that decrypts them. The API
returns only the `hints` object:

```jsonc
{
  "service": "openai", "module": "TRANSCRIPTION", "source": "USER",
  "status": "VALID", "lastVerifiedAt": "2026-08-19T08:00:00Z",
  "hints": { "apiKey": "sk-pr…9f2a" },     // first 5, last 4, never the middle
  "configured": true, "enabled": true
}
```

There is no read endpoint for plaintext, no "reveal" affordance, and no
round-trip edit: updating a field means writing a new value, which is why the
form shows a masked placeholder rather than a pre-filled input. Secrets are
redacted from logs by key name (`lib/logger.ts` denylist) as a second line of
defence.

## 6. Service catalogue

Ordered **free tiers first** within each module, so the default path through the
settings page costs nothing. Every entry carries a `tier`
(`free` / `local` / `freemium` / `paid`) and a `costNote` stating the concrete
allowance — a vague "free" claim will be wrong within six months.

| module | free / local | free credit | paid |
|---|---|---|---|
| `TRANSCRIPTION` | Groq Whisper turbo, local WhisperX | AssemblyAI, Deepgram, ElevenLabs Scribe | OpenAI Whisper / gpt-4o-transcribe |
| `EXTRACTION` | Gemini, Groq, Cerebras, Ollama (local) | OpenRouter, Mistral, Together, Cohere, Hugging Face | Anthropic, OpenAI, DeepSeek, xAI, Azure OpenAI |
| `TRANSLATION` | DeepL, LibreTranslate (local) | Google Cloud Translation | — |
| `EMBEDDING` | Voyage, Jina, local BGE | — | OpenAI embeddings |
| `INTEGRATION` | Notion, Slack, Google OAuth app | SendGrid | — |

Field shapes vary and are declared per entry: most services need a single
`apiKey`, Google needs `clientId` + `clientSecret`, Azure OpenAI needs
`apiKey` + `endpoint` + `deployment`, and `localOnly` services need nothing.

The catalogue is data (`src/lib/credentials/catalog.ts`), not scattered
conditionals: each entry declares its module, tier, fields, env fallback, docs
URL, representative models, and how to verify it. Adding a provider is one
catalogue entry and no other change. Providers speaking the OpenAI wire format
share a single verification recipe, which is why a dozen of them cost one line
each.

## 7. Degradation when a key is absent

Absence is a first-class state, never a 500 at request time:

- The module reports `unavailable` with the service name and a deep link to the
  credentials page.
- `INTEGRATIONS_MODE=mock` (SPEC-002 §2) keeps every execution path exercisable
  with no keys at all, which is what makes `docker compose up` a working demo.
- A `GET /api/credentials/status` summary drives a persistent banner listing
  which modules are live, mocked, or unavailable.

## 8. Verification

`POST /api/credentials/:service/verify` performs the catalogue's cheapest
authenticated read — `GET /v1/models` for OpenAI-shaped APIs, `GET /users/me`
for Notion, `auth.test` for Slack — and stores `VALID` / `INVALID` plus the
error. Verification is rate-limited to 1 per service per 10s per user. A key is
never verified implicitly during an unrelated request.

## 9. API

| route | purpose |
|---|---|
| `GET /api/credentials` | catalogue joined with configured state; hints only |
| `PUT /api/credentials/:service` | upsert fields; body `{ secrets: {…}, label?, enabled? }` |
| `DELETE /api/credentials/:service` | remove the row; falls back to `ENV` if present |
| `POST /api/credentials/:service/verify` | live check, records status |
| `GET /api/credentials/status` | per-module availability for the banner |

All mutations write an `AuditLog` row (`credential.saved`, `.deleted`,
`.verified`) recording the service and actor — **never** the value.

## 10. Acceptance criteria

1. With an empty `.env`, the app boots, the dashboard renders, and the
   credentials page lists every catalogue service as `NONE`/not configured.
2. Saving a key returns a masked hint; the plaintext appears in no response and
   no log line.
3. A row's ciphertext moved to a different `userId` or `service` fails to decrypt
   (AAD bind) and is reported as `INVALID` / `source: NONE`, never as configured.
4. A user key overrides the environment; deleting it falls back to `ENV`.
5. `CREDENTIALS_ENV_LOCKED=true` inverts precedence.
6. Verifying a deliberately wrong key stores `INVALID` and surfaces the message.
