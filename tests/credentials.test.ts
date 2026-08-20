import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { CATALOG, findService, servicesForModule, siblingsOf } from '@/lib/credentials/catalog'

/**
 * The credential catalogue — SPEC-004 §6.
 *
 * The regression these pin: several providers cover more than one module from a single
 * account. One Groq key serves both `/audio/transcriptions` and `/chat/completions`; one
 * OpenAI key serves audio, chat and embeddings. They were listed as separate catalogue
 * entries — correct for the UI, since the capabilities and costs genuinely differ — but
 * nothing linked them. Someone who pasted their Groq key under "Transcription" then got
 * "No extraction provider is configured", with no indication that the same key would
 * have worked or that it needed entering twice.
 */

describe('shared keys', () => {
  test('the Groq LLM entry shares the transcription key', () => {
    assert.equal(findService('groq_llm')?.sharesKeyWith, 'groq')
  })

  test('OpenAI audio and embeddings share the chat key', () => {
    assert.equal(findService('openai_audio')?.sharesKeyWith, 'openai')
    assert.equal(findService('openai_embeddings')?.sharesKeyWith, 'openai')
  })

  test('siblingsOf resolves in both directions', () => {
    // From the primary, so the UI can say "this key also covers…" where it is entered.
    const fromPrimary = siblingsOf('groq').map((s) => s.service)
    assert.ok(fromPrimary.includes('groq_llm'), `expected groq_llm, got ${fromPrimary.join(',')}`)
    // And from the dependent, so it can say "already covered by…".
    const fromDependent = siblingsOf('groq_llm').map((s) => s.service)
    assert.ok(fromDependent.includes('groq'), `expected groq, got ${fromDependent.join(',')}`)
  })

  test('a service never shares a key with itself', () => {
    for (const spec of CATALOG) {
      assert.notEqual(spec.sharesKeyWith, spec.service, `${spec.service} points at itself`)
    }
  })

  test('every sharesKeyWith names a service that exists', () => {
    for (const spec of CATALOG) {
      if (!spec.sharesKeyWith) continue
      assert.ok(findService(spec.sharesKeyWith), `${spec.service} shares with unknown ${spec.sharesKeyWith}`)
    }
  })

  test('sharing is not chained, so resolution cannot loop', () => {
    // A depends on B depends on C would need recursion the resolver deliberately bounds.
    for (const spec of CATALOG) {
      if (!spec.sharesKeyWith) continue
      const target = findService(spec.sharesKeyWith)!
      assert.equal(
        target.sharesKeyWith,
        undefined,
        `${spec.service} -> ${target.service} -> ${target.sharesKeyWith} is a chain`,
      )
    }
  })

  test('a shared entry declares no required fields of its own to collect twice', () => {
    // It may still declare them for display, but the primary is what gets asked for.
    const dependent = findService('groq_llm')!
    const primary = findService('groq')!
    assert.deepEqual(
      dependent.fields.map((f) => f.key),
      primary.fields.map((f) => f.key),
      'a shared entry should expect the same shape as its primary',
    )
  })
})

describe('catalogue integrity', () => {
  test('every service id is unique', () => {
    const ids = CATALOG.map((s) => s.service)
    assert.equal(new Set(ids).size, ids.length, `duplicates: ${ids.join(',')}`)
  })

  test('every entry is presentable', () => {
    for (const spec of CATALOG) {
      assert.ok(spec.displayName.length > 0, `${spec.service} has no display name`)
      assert.ok(spec.blurb.length > 10, `${spec.service} blurb is too thin`)
      assert.ok(spec.costNote.length > 5, `${spec.service} states no cost`)
      assert.match(spec.docsUrl, /^https:\/\//, `${spec.service} docs URL is not https`)
      assert.ok(['free', 'freemium', 'paid', 'local'].includes(spec.tier), `${spec.service} tier`)
    }
  })

  test('a non-local service asks for at least one required field', () => {
    for (const spec of CATALOG) {
      if (spec.localOnly) {
        assert.equal(spec.fields.length, 0, `${spec.service} is local but asks for a key`)
        continue
      }
      assert.ok(
        spec.fields.some((f) => f.required),
        `${spec.service} needs a key but declares no required field`,
      )
    }
  })

  test('secret fields are marked secret, so the form masks them', () => {
    for (const spec of CATALOG) {
      const key = spec.fields.find((f) => f.key === 'apiKey')
      if (key) assert.equal(key.secret, true, `${spec.service}.apiKey is not marked secret`)
      // A client id is not a secret and should not be masked — masking it makes the
      // form harder to check without protecting anything.
      const clientId = spec.fields.find((f) => f.key === 'clientId')
      if (clientId) assert.equal(clientId.secret, false, `${spec.service}.clientId is masked needlessly`)
    }
  })

  test('free and local options sort ahead of paid ones in every module', () => {
    const rank = { free: 0, local: 1, freemium: 2, paid: 3 } as const
    for (const module of ['TRANSCRIPTION', 'EXTRACTION', 'TRANSLATION', 'EMBEDDING', 'INTEGRATION'] as const) {
      const tiers = servicesForModule(module).map((s) => rank[s.tier])
      const sorted = [...tiers].sort((a, b) => a - b)
      assert.deepEqual(tiers, sorted, `${module} is not free-first`)
    }
  })

  test('every module offers at least one option that costs nothing', () => {
    for (const module of ['TRANSCRIPTION', 'EXTRACTION', 'INTEGRATION'] as const) {
      const free = servicesForModule(module).filter((s) => s.tier === 'free' || s.tier === 'local')
      assert.ok(free.length >= 1, `${module} has no free option`)
    }
  })
})
