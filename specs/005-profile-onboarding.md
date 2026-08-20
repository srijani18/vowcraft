# SPEC-005 — Profile, Settings & Onboarding

**Status:** Accepted · **Depends on:** SPEC-000, SPEC-003, SPEC-004

## 1. Goal

Give a signed-in person control over their own account — name, password,
guardrail envelope, provider routing — and a first-run walkthrough that explains
what the tool does before they are asked to approve something consequential.

## 2. Routes

| route | purpose |
|---|---|
| `/dashboard` | Landing overview: usage, workflow funnel, guardrail activity (§7) |
| `/dashboard/settings` | **Preferences** — the guardrail envelope, approval thresholds, provider routing (§4.1) |
| `/dashboard/settings/profile` | Identity, password, danger zone (§4, §8) |
| `/dashboard/settings/credentials` | BYOK vault (SPEC-004) |
| `/dashboard/settings/integrations` | OAuth connections and their state |

Preferences and identity are separate screens on purpose. Identity is about *who
you are*; preferences are about *what the agent may do on your behalf* — a much
more consequential set of switches, and one that deserves its own page rather than
being appended below a name field.

## 3. Password handling

- **scrypt** from `node:crypto`: memory-hard, standard library, no native build
  step. A native dependency that fails to compile on Alpine is a deployment
  problem, not a security improvement.
- Parameters `N=2^15, r=8, p=1`, 64-byte key, 16-byte random salt. Stored as
  `s1.<salt>.<hash>` in base64url; the `s1` prefix makes the cost factor
  upgradable **per row** so raising it later rehashes on next login instead of
  invalidating every password.
- Comparison is `timingSafeEqual`. A malformed or absent hash returns `false`
  rather than throwing, so "no password set" and "wrong password" fail
  identically from the outside.
- **Changing a password requires the current one.** A logged-in session is not
  sufficient — that is the check that stops a borrowed laptop becoming a stolen
  account. An account with no password set yet may set one without it.
- Policy: ≥12 characters, not a single repeated character, not containing the
  local part of the email or the user's name, not containing a common password.
  Deliberately **not** a maze of character-class rules: those push people toward
  `Passw0rd!` while a long passphrase scores worse.
- On change: `passwordUpdatedAt` is stamped and `profile.password_changed` is
  audited. The hash is never logged, returned, or compared client-side.

> **Scope note.** Sign-in itself remains the NextAuth seam in `src/lib/auth.ts`
> (SPEC-000 §7). This spec covers *storage, validation, and rotation* — the part
> that must be right. Wiring a NextAuth Credentials provider to `verifyPassword()`
> is the remaining step, and it is a handful of lines, not a redesign.

## 4. Profile fields

| field | rules |
|---|---|
| `name` | 1–120 chars, trimmed. Shown on cards and in the audit trail. |
| `email` | Unique. Changing it is **not** offered here: it is the account identifier and the OAuth grant subject, so a change would orphan connected integrations. Documented rather than silently omitted. |
| `image` | Optional URL, `https:` only. |

### 4.1 Preferences — `/dashboard/settings`

Every field here feeds `RuleContext.settings` (SPEC-003 §2), which means this
screen *is* the guardrail envelope. Grouped by what it governs:

| group | fields | governs |
|---|---|---|
| Working hours | `timeZone`, `workdayStart`, `workdayEnd`, `allowWeekends` | `SCHED_HOURS`, `SCHED_WEEKEND` |
| Meetings | `maxMeetingMinutes`, `minBufferMinutes` | `SCHED_MAX_DURATION`, `SCHED_BUFFER` |
| Organisation | `orgDomains`, `orgCurrency`, `budgetApprovalLimit` | risk tier for email, `VAL_BUDGET_APPROVAL` |
| Automation | `autoExecuteLowRisk`, `approvalThresholds` per action type | the approval gate (SPEC-003 §5) |
| Routing | `providerRouting` per action type | which adapter executes (§5) |

Each control states the rule id it affects, so a change has a visible consequence
rather than being a mystery knob. `autoExecuteLowRisk` defaults to **off** and is
the only switch on the page that widens what the agent may do without asking, so
it is rendered with its consequence spelled out and visually separated.
`approvalThresholds` may only *tighten* the computed gate, never loosen it — the
UI offers no option that would.

## 5. Provider routing

`UserSettings.providerRouting` maps an action type to a provider id. The UI offers
a choice only for capabilities with more than one adapter — currently `EMAIL`
(Gmail and SendGrid).

- **Gmail** sends *as the user* and can save a draft — the reversible option, so
  it is the default.
- **SendGrid** delivers *as the organisation* from a verified domain, needs no
  per-user consent, and works unattended.

SendGrid **cannot save a draft**, and `sendMode: 'draft'` is classified LOW risk
precisely because nothing is sent (SPEC-003 §3). Its `validate()` therefore
*refuses* a draft payload rather than delivering it, so routing an action to
SendGrid can never silently convert a LOW-risk approval into a real outbound
email. An override naming a provider that cannot serve the action type falls back
to the default instead of mis-executing.

## 6. Onboarding walkthrough

Shown when `User.onboardingCompletedAt` is null and `onboardingSkipped` is false.

- Sequential steps with **Back / Next / Skip tour**; the final step is **Finish**.
- Progress dots, `←`/`→`/`Esc` keyboard control, focus trapped in the dialog.
- **Skip and Finish are equally final** — both persist, and neither re-prompts on
  the next login. A tour that reappears after being dismissed reads as a bug.
- State is persisted server-side (`POST /api/profile/onboarding`), not in
  `localStorage`, so it follows the account across devices.
- Content covers, in order: what the tool does · the three readiness groups ·
  risk tiers and the approval gate · executing safely in mock mode · bringing
  your own keys · where the audit trail lives.
- Steps are declarative data in one array, so adding a step is one entry.

## 7. Landing overview

`/dashboard` answers four questions, in this order:

1. **Where does work stand?** Counts by readiness and by status, with the
   pending-decision count as the headline — the number that tells someone whether
   to open the queue.
2. **Is the workflow moving?** A funnel from extracted → decided → approved →
   executed, with the decision rate and execution success rate.
3. **What did the guardrails do?** Blocks and warnings by rule id. A guardrail
   nobody can see is a guardrail nobody trusts, and a rule that fires constantly
   is usually mis-tuned rather than heroic.
4. **What is the system's state?** Module availability, integration mode,
   connected providers, corrections captured.

Everything is computed from `AuditLog`, `ExecutionAttempt`, and `ActionItem`
aggregates — no separate analytics store, and no counters that can drift from the
rows they describe.

## 8. Account deletion

Two-step and reversible for a window:

1. `DELETE /api/profile` with `{ confirm: "<the account email>" }` sets
   `deletionRequestedAt` and audits `profile.deletion_requested`. Typing the email
   is required — a checkbox is too easy to click through for an irreversible act.
2. After a 7-day grace period a sweeper hard-deletes the `User` row. Every owned
   table cascades from it (`onDelete: Cascade`), so no orphan rows survive.
   `POST /api/profile/restore` cancels the request inside the window.

`AuditLog.actorId` is `onDelete: SetNull`, so the audit trail survives the account
that produced it. That is deliberate: deleting a user must not erase the record of
what was executed in the world on their behalf.

## 9. Acceptance criteria

1. Changing a password without the correct current password returns `403`.
2. A password under 12 characters, or containing the email local part, is refused
   with a specific reason.
3. `passwordHash` appears in no API response and no log line.
4. Renaming updates the header and writes one `profile.updated` audit row.
5. Onboarding shown once; both Skip and Finish persist and it never reappears.
6. Setting `providerRouting.EMAIL = sendgrid` routes execution to SendGrid, and a
   `sendMode: 'draft'` payload is then refused with an actionable message.
7. `DELETE /api/profile` with a mismatched confirmation returns `422` and changes
   nothing.
8. The landing overview's counts equal the dashboard's own group counts.
