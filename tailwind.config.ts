import type { Config } from 'tailwindcss'

/**
 * Colours resolve to the CSS custom properties in `globals.css`, so switching
 * theme is one attribute on <html> rather than a second set of classes on every
 * element. Channels are raw so `/opacity` modifiers keep working.
 *
 * Palette: #224248 · #325E6A · #44A1A4 · #FF9A00.
 *
 * The rule that keeps it accessible: `accent-fill` (#44A1A4) and `cta` (#FF9A00)
 * are **fills and borders, never body text** — on a pale ground they measure 2.8:1
 * and 1.9:1. Text-weight accent is `accent`, a lifted teal in dark and a darkened
 * teal in light. Both fills carry `accent-on` / `cta-on` (#0E2126) in either theme.
 */
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  darkMode: ['selector', '[data-theme="dark"]'],
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        base: token('bg'),
        surface: {
          DEFAULT: token('surface'),
          strong: token('surface-strong'),
        },
        edge: token('edge'),
        ink: {
          DEFAULT: token('ink'),
          muted: token('ink-muted'),
          faint: token('ink-faint'),
        },
        // Interactive: links, focus, active nav, secondary buttons.
        accent: {
          DEFAULT: token('accent'),
          fill: token('accent-fill'),
          on: token('accent-on'),
          // A perceivable boundary for the fill — see globals.css.
          edge: token('accent-edge'),
        },
        // The consequential action: Execute, Save, Get started.
        cta: {
          DEFAULT: token('cta'),
          on: token('cta-on'),
          edge: token('cta-edge'),
        },
        // The four given colours, addressable by name for borders and washes.
        deep: token('deep'),
        mid: token('mid'),
        teal: token('teal'),
        orange: token('orange'),
        paper: token('paper'),
        abyss: token('abyss'),

        ok: { DEFAULT: token('ok'), on: token('ok-on') },
        warn: token('warn'),
        danger: { DEFAULT: token('danger'), on: token('danger-on') },
        // Semantic aliases so risk and priority call sites stay readable.
        risk: { low: token('ok'), medium: token('warn'), high: token('danger') },
        prio: { high: token('danger'), medium: token('warn'), low: token('ink-faint') },
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      borderColor: { DEFAULT: 'rgb(var(--edge) / var(--edge-alpha))' },
      keyframes: {
        'fade-up': { from: { opacity: '0', transform: 'translateY(6px)' }, to: { opacity: '1', transform: 'none' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        'fade-up': 'fade-up 240ms cubic-bezier(0.22,1,0.36,1) both',
        shimmer: 'shimmer 1.6s infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
