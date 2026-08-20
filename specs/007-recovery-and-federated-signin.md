# SPEC-007 — Account Recovery & Federated Sign-in

**Status:** Accepted · **Extends:** SPEC-006 · **Depends on:** SPEC-002 §4 (OAuth), SPEC-004 (vault)

## 1. Purpose

Two ways back into an account that do not exist yet: a password reset for someone
who has forgotten theirs, and sign-in with Google. Both are authentication paths,
so both are worth more care than a feature of this size usually gets.

## 2. Password reset

### 2.1 Flow

```
/login → "Forgot your password?" → /forgot-password
   POST /api/auth/forgot-password { email }
   → always 200, always the same body
   → if the account exists: a single-use token is minted and emailed
/reset-password?token=… → validates on load, shows the form
   POST /api/auth/reset-password { token, newPassword }
   → sets the hash, burns the token, revokes every session, signs them in
```

### 2.2 The token

- 32 random bytes, base64url — 256 bits, so guessing is not a strategy.
- **Stored as a SHA-256 hash, never in plaintext.** A database leak must not hand
  the attacker a working reset link for every pending request. The raw token exists
  only in the email and in the URL the user clicks.
- Lookup is by hash, so it is a single indexed read rather than a scan.
- **Single-use**: `consumedAt` is stamped inside a conditional update, so two
  parallel submissions cannot both succeed.
- **One hour** to live. Long enough to find the email, short enough that a stale
  link in an inbox is not a standing key.
- Requesting a new token **invalidates every outstanding one** for that account. A
  user who clicks "resend" three times should not leave three live keys behind.

### 2.3 What it must not leak

`POST /api/auth/forgot-password` returns the same status and the same body whether
the address exists, has no password set, or was never registered. Anything else
turns the endpoint into an account enumerator — the same reason sign-in returns one
generic failure (SPEC-006 §4).

A Google-only account with no password still receives a link. Telling the user
"that address uses Google" would be exactly the leak this avoids, and the link
usefully lets them *add* a password.

Rate limits: 3 requests per address per 15 minutes, and 10 per IP per 15 minutes,
counted from the durable rows rather than process memory so restarting the server
does not reset them.

### 2.4 What a reset must do

1. Set the new hash, after the full password policy (SPEC-005 §3).
2. Stamp `passwordUpdatedAt`, which **revokes every existing session** — the
   session cookie carries that timestamp and stops validating (SPEC-006 §3). This
   matters: whoever forced the reset must not keep a session the owner cannot see.
3. Burn the token and every sibling for that account.
4. Sign the user in, so the flow ends where they wanted to be.
5. Audit `auth.password_reset_completed`.

The token is **not** accepted as proof for anything except setting a password. It
cannot change an email, and it does not grant an API session on its own.

### 2.5 Delivery

`server/email/mailer.ts` resolves a transactional sender in this order:

1. **SendGrid**, via the credential vault or `SENDGRID_API_KEY` (SPEC-004 §3).
2. **Log transport** — writes the link to the structured log and returns it to the
   caller, which the API surfaces *only* outside production.

The log transport is what makes the flow testable and self-hostable on day one
without an email provider. It is never silent: `GET /api/health` reports
`passwordResetDeliverable`, so an operator sees that a deployment has no sender
configured rather than discovering it from a confused user.

Returning the link in the API response is gated on `DEV_EXPOSE_RESET_LINK`, which
defaults to false. It is an **explicit opt-in rather than a `NODE_ENV` check**: the
standard local stack is a production build running in a container, so guessing from
the environment would either leak the link in production or make the flow
undemonstrable in the one place it most needs demonstrating. `docker-compose.yml`
enables it, because that file is unambiguously the local-testing stack.

## 3. Sign in with Google

### 3.1 Flow

```
GET  /api/auth/google           → 302 to Google (PKCE + state + nonce)
GET  /api/auth/google/callback  → exchange, verify, find-or-create, sign in
```

Reuses the `OAuthState` table and the PKCE machinery already built for integrations
(SPEC-002 §4), with two changes: `userId` becomes nullable (there is no user yet)
and the row carries a `purpose` and an OIDC `nonce`.

### 3.2 Verifying the identity

Two independent checks, because an identity is worth more than an access token:

1. **The `id_token` claims** — `iss` is `accounts.google.com` or the https form,
   `aud` equals our client id, `exp` is in the future, and `nonce` matches the one
   minted with the state. The `aud` check is the one that matters: without it, an
   id_token issued for *another* application would be accepted.
2. **The userinfo endpoint**, called with the access token, as the canonical source
   for `sub`, `email`, `email_verified`, `name`, `picture`.

The id_token's **signature is not verified**, and that is deliberate rather than an
omission: it arrived in the response body of a server-to-server POST to Google's
token endpoint over TLS, which Google's own documentation identifies as the case
where signature validation is unnecessary. The claim checks above still run, because
they guard against a token that is genuine but not *for us*.

### 3.3 `email_verified` is mandatory

An unverified Google email is refused. Without that check, anyone able to create a
Google account claiming `someone@company.com` could take over the existing local
account with that address. This is the single most important line in the feature.

### 3.4 Linking and creation

- **Known `(provider, sub)`** → sign in. `sub` is the join key, never the email:
  Google emails can change, `sub` cannot.
- **New `sub`, but the verified email matches an existing user** → link the identity
  to that account and sign in. Safe *only* because §3.3 holds.
- **Neither** → create the user with `passwordHash = null`, plus their
  `UserSettings` and first `TeamMember`, exactly as local sign-up does.

A Google-only account has no password, so `POST /api/profile/password` sets a first
one with no current password required — already the behaviour in SPEC-005 §3. Users
can therefore end up with both methods, which is the point.

`?next=` is carried through the round trip in the state row rather than the query
string, and is still restricted to same-origin paths (SPEC-006 §4).

### 3.5 Availability

The button appears only when `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are
configured. An unconfigured deployment shows local sign-in alone rather than a
button that dead-ends.

## 4. Welcome email on registration

Sent once, on a genuinely new account — local sign-up and a Google sign-in whose
outcome is `created`. A returning user, or one whose Google identity was just linked
to an existing account, is not welcomed again.

It is **fire-and-forget and cannot fail the registration.** An account that exists
but whose welcome email bounced is a minor annoyance; a sign-up that returns 500
because an email provider was down loses the user entirely. Failures are logged as
`auth.welcome_email_failed`, never raised.

Content is three things a new user needs before they touch anything consequential:
that they are in mock mode, that every action cites its source quote, and that the
guardrails run server-side and are theirs to configure. It also states which sign-in
method they used, because a Google-created account has no password and would
otherwise wonder what to type next time.

## 5. Acceptance criteria

1. `forgot-password` returns an identical status and body for a registered address,
   an unregistered one, and a Google-only account.
2. A reset token is stored hashed; the plaintext appears in no row, response, or log
   beyond the delivery payload itself.
3. A token works once. The second attempt fails, as does an expired one.
4. Requesting a new token invalidates the previous one.
5. Completing a reset invalidates every pre-existing session cookie.
6. A new password is held to the same policy as sign-up.
7. Google sign-in with `email_verified: false` is refused.
8. An `id_token` whose `aud` is another client is refused; so is a mismatched nonce.
9. A second Google sign-in reuses the same user rather than creating a duplicate.
10. Google sign-in on an email that already has a local account links to it.
11. A Google-created account can set a password without supplying a current one.
12. With no Google credentials configured, the button is absent and the route
    redirects with a readable reason rather than throwing.
13. A new account receives a welcome email; a returning or newly linked one does not.
14. A failing mail transport does not fail the registration.
