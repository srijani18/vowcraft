import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

/**
 * SPEC-003 §10 criterion 5 asserts that `domain/` imports nothing from `lib/db`,
 * `integrations/`, or `process.env`. That claim is the reason every guardrail is
 * unit-testable, so it is worth *checking* rather than trusting — a single
 * convenience import would quietly make the rule engine need a database.
 */

const FORBIDDEN: { pattern: RegExp; why: string }[] = [
  { pattern: /from '@\/lib\/db'/, why: 'the database — rules must be pure' },
  { pattern: /from '@\/integrations\//, why: 'a provider adapter — rules must not perform I/O' },
  { pattern: /from '@\/server\//, why: 'the service layer — that is the wrong direction' },
  { pattern: /process\.env/, why: 'the environment — configuration is passed in as RuleContext.settings' },
  { pattern: /\bfetch\s*\(/, why: 'the network' },
  { pattern: /\bDate\.now\s*\(\)/, why: 'the ambient clock — `now` is passed in so tests can fix it' },
  { pattern: /\bnew Date\s*\(\s*\)/, why: 'the ambient clock' },
]

async function walk(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true })
  const files: string[] = []
  for (const entry of entries) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) files.push(...(await walk(path)))
    else if (entry.name.endsWith('.ts')) files.push(path)
  }
  return files
}

test('domain/ performs no I/O and reads no ambient state', async () => {
  const files = await walk('src/domain')
  assert.ok(files.length >= 8, `expected the domain layer to have several files, found ${files.length}`)

  const violations: string[] = []
  for (const file of files) {
    const source = await readFile(file, 'utf8')
    for (const { pattern, why } of FORBIDDEN) {
      if (pattern.test(source)) violations.push(`${file} reaches for ${why} (${pattern})`)
    }
  }

  assert.deepEqual(violations, [], `domain purity broken:\n  ${violations.join('\n  ')}`)
})

test('every rule in the registry is reachable and uniquely identified', async () => {
  const { RULES } = await import('@/domain/rules/index')
  const ids = RULES.map((rule) => rule.id)

  assert.equal(new Set(ids).size, ids.length, `duplicate rule ids: ${ids.join(', ')}`)
  assert.ok(ids.length >= 17, `expected at least 17 rules, registry has ${ids.length}`)

  for (const rule of RULES) {
    assert.ok(rule.appliesTo.length > 0, `${rule.id} applies to no action type, so it can never fire`)
    assert.ok(['BLOCK', 'WARN', 'INFO'].includes(rule.severity), `${rule.id} has severity ${rule.severity}`)
    assert.equal(typeof rule.evaluate, 'function', `${rule.id} has no evaluate()`)
  }
})

test('no module uses a TypeScript parameter property', async () => {
  /*
   * `constructor(readonly x: string)` needs a real transform, not just type
   * stripping — so a single one anywhere under `lib/`, `domain/`, or `server/` makes
   * that module unimportable by `node --test` and silently removes it from the unit
   * suite's reach. Cheap to check, and the failure mode is invisible otherwise.
   */
  const files = [...(await walk('src/lib')), ...(await walk('src/domain')), ...(await walk('src/server'))]
  const offenders: string[] = []

  for (const file of files) {
    const source = await readFile(file, 'utf8')
    // A parameter list containing an accessibility or readonly modifier.
    if (/constructor\s*\([^)]*\b(readonly|public|private|protected)\s+\w/s.test(source)) {
      offenders.push(file)
    }
  }

  assert.deepEqual(
    offenders,
    [],
    `parameter properties break \`node --test\`; declare and assign the fields instead:\n  ${offenders.join('\n  ')}`,
  )
})
