# SPEC-006 — Authentication, Sessions & the Design System

**Status:** Accepted · **Supersedes:** SPEC-000 §7 (auth seam) · **Depends on:** SPEC-005

## 1. Purpose

Replace the documented identity seam with real credential authentication, and fix
the visual system — palette, contrast, and theming — as a testable contract rather
than a set of preferences.

## 2. Identity resolution

`optionalUser()` resolves in this order, and reports which tier answered:

1. **A valid signed session cookie** whose `pwd` claim still matches the account's
   `passwordUpdatedAt`. Source `session`.
2. **The seeded development identity**, only when `NODE_ENV !== 'production'` or
   `ALLOW_DEV_IDENTITY=1`. Source `dev`.
3. **Nothing.** `currentUser()` then throws `UnauthenticatedError`, which the HTTP
   boundary maps to `401 unauthenticated` and page layouts map to a redirect.

The `source` field exists for one specific reason: the auth screens redirect a
signed-in visitor to the dashboard, but must **not** do so for the development
identity — otherwise `/login` is unreachable locally and impossible to work on.

Tier 2 is what lets `docker compose up` and the smoke suite exercise the whole
product without a login step. It is gated so it cannot reach production silently.

### 2.1 Module split

`lib/session.ts` holds the token logic — payload shape, signing, verification — and
imports nothing from Next. `lib/session-cookie.ts` holds the `cookies()` read and
write. They were one module initially, which put the cryptography behind a framework
import that only resolves inside a request context: precisely the code that most
needs a unit test could not have one. The same split exists for
`password-rules.ts` (policy, client-safe) versus `password.ts` (scrypt, server-only).

## 3. Session cookie

A signed stateless cookie, not a session table: no extra round trip per request,
and the only thing it must carry is a user id.

- Name `v2b_session`; `httpOnly`, `sameSite=lax`, `secure` outside development,
  14-day lifetime.
- Format `<base64url(payload)>.<base64url(hmac-sha256)>`, compared with
  `timingSafeEqual`.
- The signing key is derived from `APP_ENCRYPTION_KEY` under a distinct label
  (`session-v1`), so there is one secret to manage and no key serves two purposes.
- Payload: `{ sub, iat, exp, pwd }`.

**`pwd` is the revocation mechanism.** It holds `passwordUpdatedAt` at issue time,
so changing a password moves the value forward and every previously issued cookie
stops validating — cheap revocation without a server-side store. The tradeoff is
stated plainly on the public security page: revocation is not per-device.

## 4. Sign-up and sign-in

`POST /api/auth/signup` · `POST /api/auth/login` · `POST /api/auth/logout`

- Sign-up applies the full password policy (SPEC-005 §3), creates `UserSettings`
  and a first `TeamMember` in the same transaction — so the rule engine always has
  a real envelope to read, and `VAL_OWNER_KNOWN` is meaningful from the first item.
- **Sign-up discloses account existence** (`409 account_exists`). The alternative
  is silently doing nothing and leaving someone stuck.
- **Sign-in never does.** Unknown address, no password set, and wrong password all
  return the same `401 invalid_credentials`, and a dummy hash runs on the
  missing-account path so the timing does not separate them either. Differentiating
  them turns the endpoint into an account enumerator.
- `?next=` is honoured only for same-origin paths beginning with a single `/`.
  Accepting an absolute URL would make the login page an open redirect.
- Logout is idempotent and clears the cookie whether or not one was present.

## 5. Palette and contrast

Palette: `#224248` · `#325E6A` · `#44A1A4` · `#FF9A00`.

| Given colour | Role |
|---|---|
| `#224248` primary dark | Panel and header backgrounds (dark); **body text** (light) |
| `#325E6A` secondary dark | Cards, inputs, raised surfaces (dark); secondary text (light) |
| `#44A1A4` accent teal | Buttons, links, highlights, focus |
| `#FF9A00` accent orange | Primary CTAs, warnings, important highlights |

### 5.1 The measurements that shaped it

| Pairing | Ratio | Consequence |
|---|---|---|
| `#224248` on `#EEF4F5` | 9.7:1 | the primary dark *is* the light-mode body text |
| `#325E6A` on `#EEF4F5` | 6.4:1 | and the secondary dark is its muted tier |
| `#E6F0F2` on `#0E2126` | 14.3:1 | dark-mode body text |
| `#44A1A4` on `#EEF4F5` | **2.8:1** | the teal cannot carry text on a pale ground |
| `#FF9A00` on `#EEF4F5` | **1.9:1** | nor can the orange, considerably less so |
| `#0E2126` on `#44A1A4` | 5.4:1 | so teal fills carry a near-black label |
| `#0E2126` on `#FF9A00` | 7.8:1 | and so do orange fills |

Hence the central rule: **both accents are fills and borders, never body text.**
Accent *text* uses a tint of the same hue — `#57B6B9` in dark (7.0:1 on the page,
4.5:1 on the `#224248` panel) and `#327779` in light (4.7:1). Both fills carry the
same `#0E2126` label in *both* themes, so a button is pixel-identical either way.

### 5.2 Why the dark page is not `#224248`

`#224248` is specified for backgrounds, and it *is* the panel and header background.
But making it the page canvas costs two things: `#325E6A` cards then separate from
it by only 1.5:1, and the teal drops to 3.5:1 — below AA for text. Deepening the
page one step on the same hue (`#0E2126`) lets **both** given darks work as real,
distinguishable surfaces and lifts the teal to 5.4:1. The derived tones are the page
canvas and the ink ladder; the four given colours are used verbatim.

### 5.3 Fills need a perceivable boundary

WCAG 1.4.11 asks for 3:1 on the *boundary* of a UI component. On the light page the
teal is 2.8:1 and the orange 1.9:1, so neither fill can carry its own edge — each
draws a darker ring from the same hue (`--accent-edge` `#327779`, `--cta-edge`
`#9E5F00`). On the dark page both clear 3:1 unaided, so the ring equals the fill and
the component markup needs no per-theme branch. The button is what has to be
findable; the fill is just paint.

### 5.4 Two accents, two jobs

The brief assigns teal to buttons *and* orange to CTAs, so they are split by weight
rather than merged: `accent` is the interactive teal (links, focus, active nav,
secondary actions) and `cta` is the orange reserved for the consequential action on
a screen — Execute, Save, Create account. A page where everything is the CTA colour
has no CTA.

### 5.5 State colours

The palette has neither a green nor a red, and orange is already doing two jobs
(CTAs and warnings). Encoding "safe" and "dangerous" in teal and orange as well
would put three meanings on one hue in a tool whose entire purpose is risk
communication. So:

- `warn` → the given orange (`#FF9A00` dark, darkened to `#9E5F00` for light text)
- `ok` → **added**: `#3FBE86` dark (7.1:1), `#0F7346` light (5.3:1)
- `danger` → **added**: `#FF8375` dark (6.9:1), `#B3261E` light (5.9:1)

Two hues outside the palette, both stated rather than smuggled in. Colour never
carries meaning alone: every badge pairs it with an icon and a text label, so the
system survives colour blindness and a monochrome print.

### 5.6 One constraint worth knowing

`#325E6A` is light enough (L = 0.097) that only `ink` and `ink-muted` clear 4.5:1
on it. `ink-faint` must not sit on a full-opacity `#325E6A`.
`tests/contrast.test.ts` measures every ink against every ground in both themes and
fails if this drifts.

## 6. Theming

Tokens are raw RGB channels on `:root`, so Tailwind's `/opacity` modifiers work
throughout. Three states, and the toggle wins in both directions:

- bare `:root` — light, the default
- `@media (prefers-color-scheme: dark)` guarded by `:root:not([data-theme='light'])`
  — the system preference, honoured only when no explicit choice exists
- `:root[data-theme='dark']` — an explicit choice

The toggle cycles **system → light → dark**, because "follow the system" is a real
state and not merely the absence of one. An inline synchronous script in `<head>`
applies the stored value before first paint, so there is no flash of the wrong
theme. The key is shared with the marketing site.

## 7. Glow

Highlight glow is a token, not a per-component shadow: `--glow-accent`,
`--glow-cta`, `--glow-card`, `--glow-card-hover`. In dark mode it is additive light
on the deep ground; in light mode it becomes a tighter coloured shadow, because a
bloom on a pale ground reads as blur rather than as light. Interactive cards shift
from a neutral resting glow to a teal hover glow, so a card that is a target *feels*
like one. CTA buttons are lit in orange at rest, accent buttons in teal.

## 8. Acceptance criteria

1. A signed-in session identifies its own user, not the development identity.
2. Rotating a password invalidates every previously issued cookie.
3. Wrong password and unknown account are indistinguishable in body and status.
4. An expired session on an API route returns `401`, never `500`.
5. `/login` is reachable in development despite the dev identity resolving.
6. `?next=https://evil.example` is ignored; `?next=/dashboard/audit-log` is honoured.
7. No password or hash appears in any response body or log line.
8. Both themes render every page with body text at ≥4.5:1 against its background,
   and every fill has a boundary at ≥3:1. Enforced by `tests/contrast.test.ts`,
   which parses the values out of `globals.css` rather than duplicating them — so it
   cannot pass while the stylesheet says something different.
9. The four given colours appear verbatim: `#224248` and `#325E6A` as the dark
   theme's two surfaces and the light theme's two ink tiers, `#44A1A4` and `#FF9A00`
   as the fills in both themes.
10. A first-time visitor with no stored preference gets their operating system's
    theme; the toggle then wins in both directions.
