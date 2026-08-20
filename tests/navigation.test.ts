import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { ALL_MODULES, findModule, groupFor, NAV_GROUPS } from '@/lib/navigation'

/**
 * The registry feeds the sidebar, the mobile drawer, and the dashboard explorer at
 * once, so a malformed entry breaks three surfaces. These checks are cheap
 * insurance against the kind of typo that only shows up as a dead link.
 */

describe('module registry — SPEC-005 §2', () => {
  test('every slug is unique', () => {
    const slugs = ALL_MODULES.map((m) => m.slug)
    assert.equal(new Set(slugs).size, slugs.length, `duplicate slugs: ${slugs.join(', ')}`)
  })

  test('every group has an id, a label, an icon, and at least one module', () => {
    for (const group of NAV_GROUPS) {
      assert.ok(group.id.length > 0)
      assert.ok(group.label.length > 0)
      assert.match(group.icon, /^bi-/)
      assert.ok(group.modules.length > 0, `${group.id} has no modules`)
    }
  })

  test('planned modules route to their own module page, never to a dead link', () => {
    for (const module of ALL_MODULES.filter((m) => m.status === 'planned')) {
      assert.equal(
        module.href,
        `/dashboard/modules/${module.slug}`,
        `${module.slug} is planned but points at ${module.href}`,
      )
    }
  })

  test('live modules point at a real dashboard route', () => {
    for (const module of ALL_MODULES.filter((m) => m.status === 'live')) {
      assert.match(module.href, /^\/dashboard/, `${module.slug} points outside the dashboard`)
      assert.ok(
        !module.href.startsWith('/dashboard/modules/'),
        `${module.slug} is live but points at the placeholder page`,
      )
    }
  })

  test('every module is presentable — icon, blurb, detail, spec, highlights', () => {
    for (const module of ALL_MODULES) {
      assert.match(module.icon, /^bi-/, `${module.slug} has icon ${module.icon}`)
      assert.ok(module.label.length > 0, `${module.slug} has no label`)
      assert.ok(module.blurb.length > 20, `${module.slug} blurb is too thin for a tooltip`)
      assert.ok(module.detail.length > 40, `${module.slug} detail is too thin for its page`)
      assert.match(module.spec, /^SPEC-\d{3}|^—/, `${module.slug} cites ${module.spec}`)
      assert.ok(module.highlights.length >= 3, `${module.slug} has ${module.highlights.length} highlights`)
    }
  })

  test('findModule resolves every registered slug and nothing else', () => {
    for (const module of ALL_MODULES) {
      assert.equal(findModule(module.slug)?.module.slug, module.slug)
      assert.ok(groupFor(module.slug))
    }
    assert.equal(findModule('does-not-exist'), null)
    assert.equal(groupFor('does-not-exist'), null)
  })

  test('the shipped surfaces are all present and marked live', () => {
    const live = new Set(ALL_MODULES.filter((m) => m.status === 'live').map((m) => m.slug))
    for (const expected of [
      'vowcraft',
      'brd-history',
      'dashboard',
      'upload',
      'transcripts',
      'action-items',
      'audit-log',
      'preferences',
      'credentials',
      'profile',
      'integrations',
    ]) {
      assert.ok(live.has(expected), `${expected} should be a live module`)
    }
  })

  /*
   * The bug this guards against shipped once: Recordings and Transcript reader were given
   * the identical href, so the sidebar's active check — which matches a route and its
   * descendants — marked both current on every transcripts URL (SPEC-012 §2.1). Two nav
   * entries pointing at one destination is always a mistake, and it is invisible in review.
   */
  test('no two modules share an href', () => {
    const byHref = new Map<string, string[]>()
    for (const module of ALL_MODULES) {
      byHref.set(module.href, [...(byHref.get(module.href) ?? []), module.slug])
    }
    const shared = [...byHref.entries()].filter(([, slugs]) => slugs.length > 1)
    assert.deepEqual(
      shared,
      [],
      `these modules share an href: ${shared.map(([href, slugs]) => `${href} <- ${slugs.join(', ')}`).join('; ')}`,
    )
  })

  test('the voice surface is the landing route — it is the tool\'s primary act (SPEC-014 §2)', () => {
    assert.equal(findModule('vowcraft')?.module.href, '/dashboard')
    // The analytics overview it displaced still has a home of its own.
    assert.equal(findModule('dashboard')?.module.href, '/dashboard/overview')
  })

  test('the settings group is reachable and last, so navigation reads top-down', () => {
    assert.equal(NAV_GROUPS[NAV_GROUPS.length - 1]!.id, 'settings')
    assert.equal(NAV_GROUPS[0]!.id, 'overview')
  })
})
