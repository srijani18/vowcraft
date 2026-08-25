# SPEC-003 — Guardrails, Approvals & Audit

**Status:** Accepted · **Implements:** HITL approval, business rules, audit · **Depends on:** SPEC-000

## 1. Principle

An agent that can act must be *unable* to act outside a stated envelope. The
envelope is code in `domain/`, pure and testable, evaluated server-side at
execute time. The UI shows the envelope; it does not enforce it.

## 2. Rule engine

```ts
type Severity = 'BLOCK' | 'WARN' | 'INFO'
interface Rule {
  id: string                  // stable, appears in API responses and audit rows
  appliesTo: ActionType[]
  severity: Severity
  evaluate(ctx: RuleContext): RuleViolation | null   // pure
}
```

`RuleContext` = `{ item, payload, now, settings, existingEvents, teamMembers }`.
All of it is passed in; the engine performs no I/O. `evaluate()` returns `null`
for "no opinion". Results are ordered `BLOCK` → `WARN` → `INFO`.

`BLOCK` prevents execution. `WARN` requires acknowledgement in the confirmation
modal and is stored in `executionResult.warnings`. `INFO` is advisory only.

## 3. Risk tiers

| tier | meaning | gate |
|---|---|---|
| `LOW` | Reversible, private to the user. Drafts, notes, own-calendar holds. | auto-execute allowed when `confidence === 'HIGH'` and no `BLOCK` |
| `MEDIUM` | Visible to colleagues, awkward to undo. Meetings with attendees, tasks assigned to others, internal messages. | explicit `APPROVED` status required |
| `HIGH` | Reaches outside the org or destroys data. External email send, payments, deletions, admin ops. | explicit `APPROVED` **plus** a typed confirmation in the modal; never auto-executed at any confidence |

Classification is computed, not stored as truth (`domain/risk.ts`):

```
EMAIL    → sendMode 'draft'                     → LOW
         → all recipients in org domains        → MEDIUM
         → any external recipient               → HIGH
CALENDAR → attendees ⊆ {self}                   → LOW
         → else                                 → MEDIUM
TASK     → assignee is self or unset            → LOW
         → else                                 → MEDIUM
REMINDER → channel 'self'                       → LOW  else MEDIUM
any      → mentions money over AUTO_APPROVE_BUDGET_LIMIT, or a destructive verb
           (delete/remove/wipe/revoke/terminate) in the description → HIGH
```

Escalation only. A rule may raise a tier, never lower one.

## 4. Rules that ship

Scheduling (`CALENDAR`, `REMINDER`):

| id | severity | rule |
|---|---|---|
| `SCHED_PAST` | BLOCK | Start must be in the future. |
| `SCHED_WEEKEND` | BLOCK | No Sat/Sun (configurable per user). |
| `SCHED_HOURS` | BLOCK | Within `workdayStart`–`workdayEnd` in the user's tz (default 09:00–18:00). |
| `SCHED_MAX_DURATION` | BLOCK | ≤ `maxMeetingMinutes` (default 120). |
| `SCHED_CONFLICT` | BLOCK | No overlap with an existing busy event. |
| `SCHED_BUFFER` | WARN | ≥ 15 min gap either side. |
| `SCHED_DND` | BLOCK | Outside "do not schedule" blocks (lunch, focus time). |

Validation (all types):

| id | severity | rule |
|---|---|---|
| `VAL_DEADLINE_PAST` | BLOCK | Deadline/dueAt not in the past. |
| `VAL_EMAIL_FORMAT` | BLOCK | Every recipient is a syntactically valid address. |
| `VAL_REQUIRED_FIELDS` | BLOCK | Required payload fields present (SPEC-001 §6.1). |
| `VAL_BUDGET_APPROVAL` | BLOCK | Money over the limit needs `managerApproved`. |

> **Removed: `VAL_OWNER_KNOWN`** (WARN — owner resolves to a `TeamMember`). It warned when
> an action's owner was not on the roster, and its remedy read "add them to the roster" — but
> no surface for editing the roster was ever built, so the only roster entry most accounts
> ever had was the one created for themselves at signup. The rule therefore fired on nearly
> every real item, offering a fix the user could not perform. The roster itself remains: it
> still grounds extraction with known spellings and resolves an owner name to the email
> address that makes an action executable (`_resolve_email`). Only the warning is gone.

Policy:

| id | severity | rule |
|---|---|---|
| `POL_EXTERNAL_EMAIL` | BLOCK unless approved | External recipients require explicit approval. |
| `POL_NO_FINANCIAL_AUTOEXEC` | BLOCK | Financial actions are never auto-executed. |
| `POL_EXPORT_CONSENT` | BLOCK | Data export requires recorded consent. |
| `POL_CONTRADICTS_DECISION` | WARN | Contradicts a recorded `Decision` from the same transcript. |
| `POL_SUPERSEDED` | BLOCK | Item has a `supersededById`; execute the successor instead. |

Adding a rule means adding one file to `domain/rules/` and one registry line.
No other layer changes — this is the extension point auditors will ask about.

## 5. Confidence-based escalation

```
effectiveGate(riskTier, confidence, settings):
  HIGH risk                            → EXPLICIT_APPROVAL_WITH_CONFIRMATION
  MEDIUM risk                          → EXPLICIT_APPROVAL
  LOW risk  + confidence HIGH + autoExecuteLowRisk → AUTO
  LOW risk  + confidence MEDIUM        → EXPLICIT_APPROVAL
  LOW risk  + confidence LOW           → EXPLICIT_APPROVAL + uncertainty banner
```

`autoExecuteLowRisk` defaults to **false**. Thresholds are per-user, per action
type, in `UserSettings.approvalThresholds`.

## 6. Where enforcement happens

Client-side checks exist only to explain. The authoritative evaluation is
`server/execution/executor.ts` steps 6–7 (SPEC-002 §5), which recomputes risk
and rules from the database row plus the normalised payload. A request cannot
pass `riskTier`, `violations`, or an approval claim; those inputs are ignored if
present.

## 7. Audit log

Append-only. No `UPDATE` or `DELETE` path exists in the codebase; the DB user in
production is granted `INSERT`/`SELECT` only on `AuditLog`.

```jsonc
{
  "id": "…", "at": "2026-08-19T09:12:03.402Z",
  "actorType": "USER",              // USER | SYSTEM | AGENT
  "actorId": "usr_…",
  "event": "action_item.executed",  // dotted, past tense
  "actionItemId": "…",
  "before": { "status": "APPROVED" },
  "after":  { "status": "EXECUTED" },
  "metadata": { "provider": "google_calendar", "externalId": "evt_…",
                "riskTier": "MEDIUM", "rules": ["SCHED_BUFFER:WARN"],
                "requestId": "req_…", "ip": "…" }
}
```

Events emitted: `action_item.created`, `.edited`, `.approved`, `.rejected`,
`.deferred`, `.execution_requested`, `.executed`, `.execution_failed`,
`.guardrail_blocked`, `integration.connected`, `integration.reauth_required`.

## 8. Learning from corrections

Every human edit writes a `Correction` row: `{ actionItemId, field, before,
after, transcriptExcerpt, at }`. A nightly job (Phase 4) selects the most recent
corrections per field and injects them as few-shot examples into the extraction
prompt. Storing them now costs one insert and makes the improvement loop a
prompt change later rather than a schema migration.

## 9. Approval timeout

`ApprovalRequest` rows carry `expiresAt` (default 48h). A sweeper marks expired
requests `EXPIRED` and their items `DEFERRED` — never auto-approved. Fail closed.

## 10. Acceptance criteria

1. A calendar action at 03:00 Sunday collects `SCHED_HOURS` + `SCHED_WEEKEND`
   as `BLOCK` and cannot execute, whatever the client sends.
2. An email to an external domain is classified `HIGH` and refuses to execute
   without explicit approval, even at `HIGH` confidence.
3. A blocked execution attempt still writes `action_item.guardrail_blocked`.
4. Every state change has exactly one corresponding `AuditLog` row.
5. `domain/` has no import of `lib/db`, `integrations/`, `server/`, `process.env`,
   `fetch`, or the ambient clock. **Enforced mechanically** by
   `tests/domain-purity.test.ts`, which also asserts every registered rule has a
   unique id, at least one applicable action type, and a valid severity.
