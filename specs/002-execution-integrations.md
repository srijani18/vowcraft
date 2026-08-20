# SPEC-002 — Execution & Integration Layer

**Status:** Accepted · **Implements:** Phase 3.3–3.5 · **Depends on:** SPEC-000, SPEC-001

## 1. Goal

Turn an approved action item into a real side effect in a third-party system,
exactly once, with a durable record of what happened — and make the whole path
runnable on a laptop with zero third-party credentials.

## 2. Operating modes

`INTEGRATIONS_MODE` selects the adapter set at process start:

| mode | behaviour | use |
|---|---|---|
| `mock` (default) | Providers simulate success, return deterministic fake ids, persist nothing outbound. Latency simulated ~120ms. | local Docker, CI, demos |
| `live` | Real HTTP calls with OAuth tokens from `IntegrationAccount`. | staging / prod |

A single provider can be forced live in an otherwise-mock process via
`INTEGRATIONS_LIVE=google_calendar,notion`. `mock` is the default *deliberately*:
an unconfigured checkout must never be one click away from emailing a stranger.
Every mock result carries `"simulated": true` and the UI labels it.

Independently, `dryRun: true` on a request validates and renders the payload
through the provider's `preview()` without calling it. Dry runs never change
`status` and never consume the idempotency key.

## 3. Provider interface

```ts
interface IntegrationProvider<P = unknown, R = unknown> {
  id: ProviderId                       // 'google_calendar' | 'notion' | 'gmail' | 'slack'
  capability: ActionType               // which action type it can satisfy
  scopes: string[]
  // OAuth
  authorizeUrl(state: string, redirectUri: string): string
  exchangeCode(code: string, redirectUri: string): Promise<TokenSet>
  refresh(refreshToken: string): Promise<TokenSet>
  // Execution
  validate(payload: unknown): Result<P>          // zod parse → typed payload
  preview(payload: P): ExecutionPreview          // human sentence + field table
  execute(payload: P, ctx: ExecutionContext): Promise<ProviderResult<R>>
}
```

`ProviderResult` is `{ externalId, url?, raw, simulated }`. Adapters throw
`ProviderError { retryable: boolean, status?, code, message }` — the classifier
in §6, not the executor, decides what is retryable.

### 3.1 Routing

`actionType → provider` resolves through the registry, honouring a per-user
override when set, else the first registered provider with that capability:

| actionType | default provider | handler |
|---|---|---|
| `CALENDAR` | `google_calendar` | `executeCalendarAction()` |
| `TASK` | `notion` | `executeTaskAction()` |
| `EMAIL` | `gmail` | `executeEmailAction()` |
| `REMINDER` | `slack` | `executeReminderAction()` |

Unroutable type → `422 no_provider` (never a silent no-op).

## 4. OAuth

Authorization Code + PKCE where supported.

- `GET /api/integrations/:provider/authorize` → 302 to the provider. `state` is
  a signed, single-use, 10-minute token bound to the user id; the PKCE verifier
  is stored alongside it.
- `GET /api/integrations/:provider/callback` → verifies `state`, exchanges the
  code, upserts `IntegrationAccount`.
- **Refresh tokens are encrypted at rest** with AES-256-GCM under
  `APP_ENCRYPTION_KEY` (32 bytes, base64). Ciphertext format
  `v1.<iv>.<tag>.<ct>`, all base64url. Access tokens are stored encrypted too.
- `withFreshToken(account, provider)` refreshes when `expiresAt` is within 120s,
  serialises concurrent refreshes per account, and persists the new token set.
  A `401` from a provider triggers exactly one forced refresh + retry; a second
  `401` marks the account `needsReauth` and surfaces `409 reauth_required`.
- Tokens are never logged, never returned by any API, never sent to the client.

## 5. Execution pipeline

`POST /api/action-items/:id/execute` runs these steps in order. Any failure
short-circuits and is recorded.

1. **Load** item + transcript + user. 404 if absent.
2. **Payload merge** — stored `payload` deep-merged with `payloadOverride`, then
   normalised (trim, dedupe and lowercase recipients, coerce dates to ISO UTC).
   This happens *first* so the idempotency key and the preview describe the exact
   bytes that will be sent.
3. **Idempotency** — key defaults to `sha256(itemId + ':' + stableStringify(payload))`.
   A prior `SUCCESS` `ExecutionAttempt` with the same key short-circuits and
   returns the original result with `"replayed": true`.
   This is checked **before** the status gate deliberately: repeating an
   *identical* request is then idempotent, which is the correct semantics for a
   keyed operation and the only defence against a double-clicked Execute button
   that survives a process restart.
4. **Status gate** — must be `APPROVED` or `FAILED`. An `EXECUTED` item reached
   with a *different* payload fails here (`409 already_executed`) rather than
   quietly executing something new under an old approval. `EXECUTING` →
   `409 execution_in_progress`.
5. **Dependency gate** — a `dependsOnId` that is not yet `EXECUTED` →
   `409 blocked_by_dependency` (§8).
6. **Validate** — provider `validate()`. Failure → `422 invalid_payload` with
   field paths.
7. **Guardrails** — re-run the full rule engine server-side (SPEC-003 §4).
   Any `BLOCK` → `422 guardrail_blocked` listing rule ids. `WARN` results are
   attached to the response, not fatal.
8. **Approval gate** — risk tier vs. recorded approval (SPEC-003 §5).
   Insufficient → `409 approval_required`.
9. **Claim** — transition to `EXECUTING` with a conditional update on the status
   just read, so two concurrent requests cannot both enter dispatch. Open an
   `ExecutionAttempt` row.
10. **Dispatch** with retry (§6) inside a 20s per-attempt timeout. The access
    token is fetched per attempt, so a refresh mid-retry takes effect.
11. **Record** — on success: `status = EXECUTED`, `executionResult` written (§7),
    attempt closed `SUCCESS`. On terminal failure: `status = FAILED`,
    `executionResult` holds the error, attempt closed `FAILED`.
12. **Audit** — one `AuditLog` row for the outcome, always, including failures.

Steps 11 and 12 share a single transaction, so an item can never be `EXECUTED`
without its audit row.

A `dryRun: true` request stops after step 8 and returns the preview, the risk
assessment, and any warnings. It never changes `status`, never opens an
`ExecutionAttempt`, and never consumes the idempotency key.

## 6. Retries

Retry only what can succeed later: `429`, `500`, `502`, `503`, `504`, connection
resets, timeouts. Never retry `400`, `403`, `404`, `409`, `422` — a malformed
event is malformed on the third try too.

Exponential backoff with full jitter: `min(8000, 400 · 2^n) · rand(0.5..1)`,
3 attempts total. `Retry-After` on a `429` overrides the computed delay when it
is shorter than 30s. Every attempt increments `executionAttempts` and appends to
`executionResult.attempts[]` so a support engineer can see the whole history on
one row.

Mutating providers are retried only when the failure happened **before** a
provider id was returned, or when the provider is idempotent for the key we
sent. Gmail send is treated as non-idempotent: a timeout after dispatch marks
`FAILED` with `uncertain: true` rather than resending. Silence is preferable to
a duplicate email to a customer.

## 7. `executionResult` shape

One JSON column, versioned, written on success *and* failure:

```jsonc
{
  "version": 1,
  "outcome": "SUCCESS",              // SUCCESS | FAILED | SKIPPED
  "provider": "google_calendar",
  "mode": "mock",                    // mock | live
  "simulated": true,
  "externalId": "evt_mock_8f21c0",
  "externalUrl": "https://calendar.google.com/…",
  "summary": "Created “Budget review” on Fri 22 Aug, 10:00–10:30 IST for 3 guests.",
  "startedAt": "2026-08-19T09:12:03.114Z",
  "finishedAt": "2026-08-19T09:12:03.402Z",
  "durationMs": 288,
  "attempts": [ { "n": 1, "outcome": "SUCCESS", "durationMs": 288 } ],
  "warnings": [ { "ruleId": "SCHED_BUFFER", "message": "Only 5 min before next meeting." } ],
  "payloadUsed": { "…": "the exact normalised payload sent" },
  "error": null                      // { code, message, retryable, uncertain? } when FAILED
}
```

`payloadUsed` is what makes an execution reproducible after the fact; it is
recorded even for failures. Recipient emails are stored, tokens never are.

## 8. Multi-step workflows (forward compatibility)

`ActionItem.parentId` + `stepOrder` model sub-tasks ("onboard a new hire" → five
children). The executor runs children in `stepOrder`, halting the chain on the
first terminal failure and marking the remainder `SKIPPED` with a reason. The
parent's `executionResult.outcome` is `SUCCESS` only if every child succeeded.
`dependsOnId` blocks execution until the dependency is `EXECUTED`
(`409 blocked_by_dependency`). The schema and gates ship now; the fan-out
orchestrator is Phase 4.

## 9. Acceptance criteria

1. With no credentials and `INTEGRATIONS_MODE=mock`, all four action types
   execute end-to-end and produce `executionResult` per §7.
2. Executing the same item twice with the same payload returns the first result
   with `replayed: true` and creates no second side effect. Executing an already
   executed item with a *different* payload returns `409 already_executed`.
3. A provider `500` is retried 3× with growing delays then recorded `FAILED`;
   a `400` is recorded `FAILED` after exactly one attempt.
4. `dryRun: true` returns a preview, leaves `status` unchanged, writes no
   `ExecutionAttempt`.
5. No token value appears in any log line, API response, or `executionResult`.
