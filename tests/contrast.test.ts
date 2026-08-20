import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

/**
 * The palette, checked against WCAG 2.1 — SPEC-006 §5.
 *
 * Values are parsed out of `globals.css` rather than duplicated here, so this
 * cannot pass while the stylesheet says something different. Every ink is measured
 * against every ground it can actually appear on, in both themes: a token that
 * reads well on the page but not on a card is a real defect, and it is exactly the
 * kind that survives a visual review on one screen.
 */

// ── WCAG relative luminance and contrast ratio
function channel(value: number): number {
  const c = value / 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function luminance([r, g, b]: [number, number, number]): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrast(a: [number, number, number], b: [number, number, number]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

// ── parse `--name: r g b;` out of a block
const css = readFileSync('src/app/globals.css', 'utf8')

function block(header: string): Record<string, [number, number, number]> {
  const start = css.indexOf(header)
  assert.notEqual(start, -1, `could not find the CSS block: ${header}`)
  // Read to the end of this declaration block.
  const end = css.indexOf('color-scheme:', start)
  const body = css.slice(start, end)
  const found: Record<string, [number, number, number]> = {}
  for (const [, name, r, g, b] of body.matchAll(/--([a-z-]+):\s*(\d+)\s+(\d+)\s+(\d+);/g)) {
    found[name!] = [Number(r), Number(g), Number(b)]
  }
  return found
}

const light = block(':root {')
const dark = block(":root[data-theme='dark'] {")

const AA = 4.5
const AA_LARGE = 3
const hex = ([r, g, b]: [number, number, number]) =>
  `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('').toUpperCase()}`

describe('the four given colours are present verbatim', () => {
  const GIVEN = {
    deep: [34, 66, 72],
    mid: [50, 94, 106],
    teal: [68, 161, 164],
    orange: [255, 154, 0],
  } as const

  for (const [name, expected] of Object.entries(GIVEN)) {
    test(`--${name} is ${hex(expected as [number, number, number])}`, () => {
      assert.deepEqual(light[name], expected, `--${name} drifted from the given palette`)
    })
  }

  test('the given darks are the dark theme’s surfaces, as specified', () => {
    assert.deepEqual(dark.surface, GIVEN.deep, 'primary dark should be the panel background')
    assert.deepEqual(dark['surface-strong'], GIVEN.mid, 'secondary dark should be the card background')
  })

  test('the given darks are the light theme’s text, as specified', () => {
    assert.deepEqual(light.ink, GIVEN.deep)
    assert.deepEqual(light['ink-muted'], GIVEN.mid)
  })

  test('the given teal and orange are the fills in both themes', () => {
    assert.deepEqual(light['accent-fill'], GIVEN.teal)
    assert.deepEqual(dark['accent-fill'], GIVEN.teal)
    assert.deepEqual(light.cta, GIVEN.orange)
    assert.deepEqual(dark.cta, GIVEN.orange)
  })
})

for (const [themeName, tokens, grounds] of [
  ['light', light, ['bg', 'surface', 'surface-strong']],
  ['dark', dark, ['bg', 'surface', 'surface-strong']],
] as const) {
  describe(`${themeName} theme — body text clears AA on every ground`, () => {
    // `ink-faint` is excluded from `surface-strong` deliberately: in dark mode that
    // ground is #325E6A (L=0.097), light enough that only ink and ink-muted clear
    // 4.5:1 on it. The rule is documented in globals.css and asserted below.
    for (const inkName of ['ink', 'ink-muted']) {
      for (const groundName of grounds) {
        test(`${inkName} on ${groundName}`, () => {
          const ratio = contrast(tokens[inkName]!, tokens[groundName]!)
          assert.ok(
            ratio >= AA,
            `${inkName} ${hex(tokens[inkName]!)} on ${groundName} ${hex(tokens[groundName]!)} = ${ratio.toFixed(2)}:1, needs ${AA}`,
          )
        })
      }
    }

    for (const groundName of ['bg', 'surface']) {
      test(`ink-faint on ${groundName}`, () => {
        const ratio = contrast(tokens['ink-faint']!, tokens[groundName]!)
        assert.ok(ratio >= AA, `ink-faint on ${groundName} = ${ratio.toFixed(2)}:1, needs ${AA}`)
      })
    }
  })

  describe(`${themeName} theme — accent and state colours`, () => {
    for (const name of ['accent', 'warn', 'ok', 'danger']) {
      for (const groundName of ['bg', 'surface']) {
        test(`${name} on ${groundName}`, () => {
          const ratio = contrast(tokens[name]!, tokens[groundName]!)
          assert.ok(
            ratio >= AA,
            `${name} ${hex(tokens[name]!)} on ${groundName} = ${ratio.toFixed(2)}:1, needs ${AA}`,
          )
        })
      }
    }

    test('a fill label clears AA on its own fill', () => {
      const onAccent = contrast(tokens['accent-on']!, tokens['accent-fill']!)
      const onCta = contrast(tokens['cta-on']!, tokens.cta!)
      assert.ok(onAccent >= AA, `accent-on on accent-fill = ${onAccent.toFixed(2)}:1`)
      assert.ok(onCta >= AA, `cta-on on cta = ${onCta.toFixed(2)}:1`)
    })

    test('the fills are NOT usable as body text — which is why `accent` exists', () => {
      // Documenting the constraint that shaped the whole system. If a future palette
      // made these legible, this test should be revisited, not deleted.
      const asText = contrast(tokens['accent-fill']!, tokens.bg!)
      const ctaAsText = contrast(tokens.cta!, tokens.bg!)
      if (themeName === 'light') {
        assert.ok(asText < AA, `teal reads ${asText.toFixed(2)}:1 on the light page — update the docs`)
        assert.ok(ctaAsText < AA, `orange reads ${ctaAsText.toFixed(2)}:1 on the light page`)
      }
    })

    test('every fill has a perceivable boundary — WCAG 1.4.11', () => {
      /*
       * 1.4.11 asks for 3:1 on the *boundary* of a UI component, not on its
       * interior. On the light page the teal is 2.8:1 and the orange 1.9:1, so
       * neither fill can carry its own edge — each draws a darker ring instead,
       * which is why `accent-edge` and `cta-edge` exist. The button is what has to
       * be findable; the fill is just paint.
       */
      for (const [fill, edge] of [
        ['accent-fill', 'accent-edge'],
        ['cta', 'cta-edge'],
      ] as const) {
        const boundary = Math.max(
          contrast(tokens[fill]!, tokens.bg!),
          contrast(tokens[edge]!, tokens.bg!),
        )
        assert.ok(
          boundary >= AA_LARGE,
          `${fill} has no perceivable boundary: fill ${contrast(tokens[fill]!, tokens.bg!).toFixed(2)}:1, ` +
            `ring ${contrast(tokens[edge]!, tokens.bg!).toFixed(2)}:1 — needs ${AA_LARGE}`,
        )
      }
    })

    test('surfaces are distinguishable from the page', () => {
      for (const groundName of ['surface', 'surface-strong']) {
        const ratio = contrast(tokens[groundName]!, tokens.bg!)
        assert.ok(ratio >= 1.05, `${groundName} is indistinguishable from the page (${ratio.toFixed(2)}:1)`)
      }
    })
  })
}

describe('both themes define the same token set', () => {
  test('no token exists in one theme and not the other', () => {
    // A token defined only under one theme renders as `rgb( / 1)` in the other,
    // which the browser drops silently — the element just loses its colour.
    const themed = [
      'bg', 'surface', 'surface-strong', 'ink', 'ink-muted', 'ink-faint',
      'accent', 'accent-fill', 'accent-on', 'accent-edge', 'cta', 'cta-on', 'cta-edge',
      'ok', 'ok-on', 'warn', 'danger', 'danger-on',
    ]
    for (const name of themed) {
      assert.ok(light[name], `--${name} missing from the light theme`)
      assert.ok(dark[name], `--${name} missing from the dark theme`)
    }
  })
})

describe('the marketing site shares these definitions', () => {
  test('its token values are identical, not merely similar', () => {
    const other = '/Users/srijaniguharay/Desktop/Development/voice2brd-marketing/src/app/globals.css'
    let marketingCss: string
    try {
      marketingCss = readFileSync(other, 'utf8')
    } catch {
      // The sibling repo is optional; skip rather than fail a standalone checkout.
      return
    }
    const ours = css.slice(css.indexOf(':root {'), css.indexOf('color-scheme: light'))
    const theirs = marketingCss.slice(marketingCss.indexOf(':root {'), marketingCss.indexOf('color-scheme: light'))
    const values = (block: string) => [...block.matchAll(/--([a-z-]+):\s*(\d+ \d+ \d+);/g)].map((m) => `${m[1]}=${m[2]}`)
    assert.deepEqual(values(ours), values(theirs), 'the two properties have drifted apart')
  })
})
