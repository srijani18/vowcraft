import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { MIN_PASSWORD_LENGTH, scorePassword, validateConfirmation, validateEmail, validateName, validatePassword } from '@/lib/password-rules'
import { hashPassword, verifyPassword } from '@/lib/password'

describe('password policy — SPEC-005 §3', () => {
  test('length is the primary gate, and the message counts what is missing', () => {
    const problem = validatePassword('shortish')
    assert.equal(problem?.code, 'password_too_short')
    assert.match(problem!.message, /4 more characters/)
  })

  test('a long passphrase passes without any symbol gymnastics', () => {
    // The policy deliberately rewards length over character classes.
    assert.equal(validatePassword('seventeen blue lanterns'), null)
  })

  test('a single repeated character is refused however long', () => {
    assert.equal(validatePassword('aaaaaaaaaaaaaaaaaaaa')?.code, 'password_repetitive')
  })

  describe('identity checks — by proportion, not bare substring', () => {
    /*
     * The regression this pins: the check used to reject any password *containing* the
     * email's local part, with a three-character floor. That refused "a memorable phrase
     * you will recall" for anyone at `you@…`, because "you" is also an English word —
     * and the same trap caught `me@`, `dev@`, `ops@`, `sam@`. The intent was never
     * "these letters must not appear"; it was "your password must not *be* your name or
     * address".
     */
    test('a password that essentially IS the email local part is refused', () => {
      assert.equal(validatePassword('marcusmarcus', { email: 'marcus@acme.test' })?.code, 'password_contains_email')
      assert.equal(validatePassword('marcus1234567', { email: 'marcus@acme.test' })?.code, 'password_contains_email')
      assert.equal(validatePassword('priya.raman1234', { email: 'priya.raman@acme.test' })?.code, 'password_contains_email')
    })

    test('a real passphrase that merely contains it is accepted', () => {
      assert.equal(validatePassword('a memorable phrase you will recall', { email: 'you@acme.test' }), null)
      assert.equal(validatePassword('developers write good passphrases', { email: 'dev@acme.test' }), null)
      assert.equal(validatePassword('sam went to the market today ok', { email: 'sam@acme.test', name: 'Sam' }), null)
    })

    test('a password that essentially IS the name is refused', () => {
      assert.equal(validatePassword('marcusmarcus', { name: 'Marcus' })?.code, 'password_contains_name')
      assert.equal(validatePassword('marcus1234567', { name: 'Marcus' })?.code, 'password_contains_name')
    })

    test('a passphrase built around the name is accepted', () => {
      assert.equal(validatePassword('marcus vale runs the whole show', { name: 'Marcus Vale' }), null)
    })

    test('a very short local part is ignored entirely', () => {
      assert.equal(validatePassword('a long enough phrase', { email: 'ab@acme.test' }), null)
    })
  })

  describe('common passwords — matched as the whole password, not as a substring', () => {
    /*
     * The regression this pins: an earlier version rejected any password *containing* a
     * common word, which refused strong passphrases like "a password that should
     * survive" while the genuinely weak cases were already caught by the length rule.
     * Substring matching on a wordlist punishes exactly the people writing good
     * passphrases.
     */
    test('a long passphrase containing a common word is accepted', () => {
      for (const good of [
        'a password that should survive',
        'my welcome home party plans',
        'the qwerty keyboard is fine',
        'admin access is restricted here',
        'i love you more than dragons',
      ]) {
        assert.equal(validatePassword(good), null, `wrongly refused: ${good}`)
      }
    })

    test('a common password padded with digits is still refused', () => {
      for (const bad of [
        'password12345', 'welcome123456', '1234qwerty1234', 'iloveyou2026',
        'qwertyuiop1234', '123456789012',
      ]) {
        assert.equal(validatePassword(bad)?.code, 'password_common', `wrongly accepted: ${bad}`)
      }
    })

    test('a common password padded with punctuation or repeated is refused', () => {
      assert.equal(validatePassword('Password1234!')?.code, 'password_common')
      assert.equal(validatePassword('passwordpassword')?.code, 'password_common')
      assert.equal(validatePassword('letmeinletmein')?.code, 'password_common')
    })
  })

  test('a ceiling exists, so nothing pathological reaches the hasher', () => {
    assert.equal(validatePassword('a'.repeat(201) + 'bcd')?.code, 'password_too_long')
  })

  test('an empty password is refused by presence, not by length', () => {
    assert.equal(validatePassword('')?.code, 'password_required')
  })

  test('every problem carries a field, a code, and a message', () => {
    const problem = validatePassword('x')!
    assert.equal(problem.field, 'password')
    assert.ok(problem.code.length > 0)
    assert.ok(problem.message.length > 0)
  })

  test('the minimum is a single exported constant', () => {
    assert.equal(MIN_PASSWORD_LENGTH, 12)
    assert.equal(validatePassword('a'.repeat(MIN_PASSWORD_LENGTH - 1))?.code, 'password_too_short')
    assert.equal(validatePassword('correct horse b'), null)
  })
})

describe('scrypt hashing — SPEC-005 §3', () => {
  test('a hash verifies against its own password', async () => {
    const hash = await hashPassword('seventeen blue lanterns')
    assert.ok(await verifyPassword('seventeen blue lanterns', hash))
  })

  test('a wrong password does not verify', async () => {
    const hash = await hashPassword('seventeen blue lanterns')
    assert.ok(!(await verifyPassword('eighteen blue lanterns', hash)))
  })

  test('the stored form is versioned and salted, never a bare digest', async () => {
    const hash = await hashPassword('seventeen blue lanterns')
    const parts = hash.split('.')
    assert.equal(parts[0], 's1')
    assert.equal(parts.length, 3)
    assert.ok(!hash.includes('seventeen'))
  })

  test('the same password hashes differently — a per-account salt', async () => {
    const a = await hashPassword('seventeen blue lanterns')
    const b = await hashPassword('seventeen blue lanterns')
    assert.notEqual(a, b)
    assert.ok(await verifyPassword('seventeen blue lanterns', a))
    assert.ok(await verifyPassword('seventeen blue lanterns', b))
  })

  test('a null hash returns false rather than throwing', async () => {
    // "No password set" and "wrong password" must fail identically from outside.
    assert.equal(await verifyPassword('anything', null), false)
  })

  test('a malformed or foreign hash returns false rather than throwing', async () => {
    assert.equal(await verifyPassword('x', 'garbage'), false)
    assert.equal(await verifyPassword('x', 's2.aaa.bbb'), false)
    assert.equal(await verifyPassword('x', '$2b$10$abcdefghijklmnop'), false)
  })

  test('unicode is normalised, so the same phrase typed two ways still verifies', async () => {
    // NFKC: a composed é and a decomposed e+◌́ are the same password.
    const hash = await hashPassword('café passphrase here')
    assert.ok(await verifyPassword('café passphrase here', hash))
  })
})

describe('field validators', () => {
  test('email', () => {
    assert.equal(validateEmail('a@b.co'), null)
    assert.equal(validateEmail('')?.code, 'email_required')
    assert.equal(validateEmail('nope')?.code, 'email_invalid')
    assert.equal(validateEmail(`${'a'.repeat(250)}@b.co`)?.code, 'email_too_long')
  })

  test('name', () => {
    assert.equal(validateName('Priya'), null)
    assert.equal(validateName(' ')?.code, 'name_required')
    assert.equal(validateName('P')?.code, 'name_too_short')
    assert.equal(validateName('x'.repeat(121))?.code, 'name_too_long')
  })

  test('confirmation', () => {
    assert.equal(validateConfirmation('abc', 'abc'), null)
    assert.equal(validateConfirmation('abc', '')?.code, 'confirmation_required')
    assert.equal(validateConfirmation('abc', 'abd')?.code, 'confirmation_mismatch')
  })
})

describe('strength meter', () => {
  test('scores on length, and reports how far short', () => {
    assert.equal(scorePassword('').score, 0)
    const short = scorePassword('abcdef')
    assert.equal(short.label, 'Too short')
    assert.match(short.hint, /6 more/)
  })

  test('a long passphrase scores strong without symbols', () => {
    assert.equal(scorePassword('seventeen blue lanterns').label, 'Strong')
  })

  test('the percentage is bounded', () => {
    for (const value of ['', 'a', 'abcdefghijkl', 'a'.repeat(60)]) {
      const { pct } = scorePassword(value)
      assert.ok(pct >= 0 && pct <= 100, `pct out of range for length ${value.length}: ${pct}`)
    }
  })
})
