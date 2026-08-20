/**
 * Client-safe password rules and the strength meter.
 *
 * Separate from `lib/password.ts` because that module imports `node:crypto` and
 * cannot be bundled for the browser. This file holds only the *policy* — the same
 * rules, expressed without any hashing — so the form can give immediate feedback
 * and the server can still be the authority. `MIN_PASSWORD_LENGTH` is re-exported
 * from here to keep one number rather than two that can drift.
 */

export const MIN_PASSWORD_LENGTH = 12

/*
 * Weak passwords, matched as the *whole* password rather than as a substring.
 *
 * An earlier version rejected any password containing one of these anywhere, which
 * refused "a password that should survive", "my welcome home party plans", and "the
 * qwerty keyboard is fine" — all strong passphrases — while the genuinely weak cases
 * ("password", "letmein") were already caught by the length rule. Substring matching
 * on a wordlist punishes exactly the people writing good passphrases.
 */
const COMMON = [
  'password', 'letmein', 'welcome', 'qwerty', 'iloveyou', 'admin', 'monkey',
  'dragon', 'football', 'baseball', 'sunshine', 'princess', 'trustno1',
  '12345678', '123456789', '1234567890', 'qwertyuiop', 'abc123',
]

/**
 * True when the password *is* a common password, or a common password with a trivial
 * decoration — trailing digits, a bang, a capitalised first letter. `Password1!` and
 * `welcome2024` are the same password as `password` and `welcome` for cracking
 * purposes; `my welcome home party plans` is not.
 */
function isCommonPassword(password: string): boolean {
  // Compare on letters and digits only, so punctuation decoration does not disguise it.
  const stripped = password.toLowerCase().replace(/[^a-z0-9]/g, '')
  if (!stripped) return false

  for (const common of COMMON) {
    if (stripped === common) return true

    /*
     * A common word padded only with digits, on either side: `password12345`,
     * `welcome2024`, `123qwerty`. Any run of digits, not a bounded one — appending
     * five digits instead of two does not make `password` a different password.
     */
    if (new RegExp(`^\\d*${common}\\d*$`).test(stripped)) return true

    // The same word repeated to reach a length minimum: passwordpassword.
    if (stripped === common.repeat(2) || stripped === common.repeat(3)) return true

    /*
     * Or a common word plus a few non-digit characters, so `password!!!!` is caught
     * while a passphrase that merely contains the word is not. Three characters is the
     * threshold: beyond that there is real material in there.
     */
    if (stripped.startsWith(common) && stripped.length - common.length <= 3) return true
  }
  return false
}

/**
 * Carries all three pieces every caller needs: `field` for the form to attach the
 * message to an input, `code` for the API's stable error envelope, and `message`
 * for the human. One shape rather than two that must be kept in sync.
 */
export interface FieldError {
  field: string
  code: string
  message: string
}

export function validateEmail(email: string): FieldError | null {
  const value = email.trim()
  if (!value) return { field: 'email', code: 'email_required', message: 'Enter your email address.' }
  // Deliberately permissive: the only authority on whether an address exists is
  // sending to it, and an over-strict regex rejects valid addresses.
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value)) {
    return { field: 'email', code: 'email_invalid', message: 'That does not look like an email address.' }
  }
  if (value.length > 254) return { field: 'email', code: 'email_too_long', message: 'That email address is too long.' }
  return null
}

export function validateName(name: string): FieldError | null {
  const value = name.trim()
  if (!value) return { field: 'name', code: 'name_required', message: 'Enter your name.' }
  if (value.length < 2) return { field: 'name', code: 'name_too_short', message: 'That is too short to be a name.' }
  if (value.length > 120) return { field: 'name', code: 'name_too_long', message: 'Keep it under 120 characters.' }
  return null
}

/**
 * True when `needle` accounts for most of `haystack` — so the password *is* the name or
 * address, rather than merely containing it somewhere.
 *
 * "marcus" and "marcus2024" are the same password as far as an attacker who knows the
 * account is concerned. "a memorable phrase you will recall" is not, even though it
 * contains "you".
 */
function dominates(needle: string, haystack: string): boolean {
  if (!haystack.includes(needle)) return false
  // Half or more of the material is the identifier itself.
  if (needle.length * 2 >= haystack.length) return true
  // Or everything around it is just digits: marcus1234567.
  const remainder = haystack.split(needle).join('')
  return /^\d*$/.test(remainder)
}

/**
 * Length first, then variety — the same policy the app enforces. Not a maze of
 * character-class rules: those push people toward `Passw0rd!` while a long
 * passphrase scores worse.
 */
export function validatePassword(password: string, context: { email?: string; name?: string } = {}): FieldError | null {
  const value = password
  if (!value) return { field: 'password', code: 'password_required', message: 'Choose a password.' }

  if (value.length < MIN_PASSWORD_LENGTH) {
    const short = MIN_PASSWORD_LENGTH - value.length
    return {
      field: 'password',
      code: 'password_too_short',
      message: `${short} more character${short === 1 ? '' : 's'} needed — ${MIN_PASSWORD_LENGTH} minimum. A memorable phrase is stronger than a short scramble.`,
    }
  }
  if (value.length > 200) return { field: 'password', code: 'password_too_long', message: 'Keep it under 200 characters.' }
  if (/^(.)\1+$/.test(value)) {
    return { field: 'password', code: 'password_repetitive', message: 'That is a single character repeated.' }
  }

  /*
   * Identity checks, by *proportion* rather than by bare substring.
   *
   * The rule these replace rejected any password containing the email's local part
   * anywhere, with a three-character floor. That refused "a memorable phrase you will
   * recall" for anyone whose address starts `you@`, because "you" is also an English
   * word — and the same trap catches `me@`, `dev@`, `ops@`, `sam@`. The intent was
   * never "these letters must not appear"; it was "your password must not *be* your
   * name or address".
   */
  const stripped = value.toLowerCase().replace(/[^a-z0-9]/g, '')
  const local = context.email?.split('@')[0]?.toLowerCase().replace(/[^a-z0-9]/g, '')
  if (local && local.length >= 3 && dominates(local, stripped)) {
    return {
      field: 'password',
      code: 'password_contains_email',
      message: 'Your password is essentially your email address. Pick something unrelated to it.',
    }
  }

  const name = context.name?.trim().toLowerCase().replace(/[^a-z0-9]/g, '')
  if (name && name.length >= 4 && dominates(name, stripped)) {
    return {
      field: 'password',
      code: 'password_contains_name',
      message: 'Your password is essentially your name. Pick something unrelated to it.',
    }
  }
  if (isCommonPassword(value)) {
    return {
      field: 'password',
      code: 'password_common',
      message:
        'That is one of the most commonly used passwords, or a small variation on one. Pick ' +
        'something less guessable — a few unrelated words works well.',
    }
  }
  return null
}

export function validateConfirmation(password: string, confirmation: string): FieldError | null {
  if (!confirmation) return { field: 'confirmPassword', code: 'confirmation_required', message: 'Repeat your password.' }
  if (password !== confirmation) return { field: 'confirmPassword', code: 'confirmation_mismatch', message: 'The two passwords do not match.' }
  return null
}

export interface Strength {
  score: 0 | 1 | 2 | 3
  label: string
  hint: string
  pct: number
}

/** Advisory meter. Scored on length because that is what resists cracking. */
export function scorePassword(password: string): Strength {
  const length = password.length
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((re) => re.test(password)).length

  if (length === 0) return { score: 0, label: '', hint: '', pct: 0 }
  if (length < MIN_PASSWORD_LENGTH) {
    return {
      score: 0,
      label: 'Too short',
      hint: `${MIN_PASSWORD_LENGTH - length} more to go.`,
      pct: Math.round((length / MIN_PASSWORD_LENGTH) * 33),
    }
  }
  if (length >= 20 || (length >= 16 && classes >= 3)) {
    return { score: 3, label: 'Strong', hint: 'Long enough to resist offline cracking.', pct: 100 }
  }
  if (length >= MIN_PASSWORD_LENGTH + 4 || classes >= 3) {
    return { score: 2, label: 'Good', hint: 'More length helps more than more symbols.', pct: 70 }
  }
  return { score: 1, label: 'Acceptable', hint: 'Consider a longer passphrase.', pct: 45 }
}
