'use client'

import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/primitives'
import { apiFetch } from '@/lib/api-client'

/**
 * First-run walkthrough — SPEC-005 §6.
 *
 * Two decisions worth naming:
 *
 *  1. **Skip and Finish are equally final.** Both persist the same terminal state
 *     server-side, so a dismissed tour never reappears. A tour that comes back
 *     after being skipped reads as a bug and trains people to distrust the UI.
 *  2. **State lives on the server, not in localStorage**, so it follows the
 *     account across devices rather than re-running on every new browser.
 *
 * Steps are declarative data: adding one is a single array entry.
 */

interface Step {
  icon: string
  title: string
  body: string
  points?: string[]
  tone?: 'neutral' | 'caution'
}

const STEPS: readonly Step[] = [
  {
    icon: 'bi-mic-fill',
    title: 'Speak a requirement, get a document',
    body:
      'The screen behind this has one button. Press it, describe what you need built, and press stop ' +
      '\u2014 you get a structured business requirements document written from what you said.',
    points: [
      'Transcription appears live while you talk',
      'Speak again afterwards to amend the document, not regenerate it',
      'Anything it cannot infer becomes an open question rather than an invention',
    ],
  },
  {
    icon: 'bi-soundwave',
    title: 'Conversations become actions',
    body:
      'Vowcraft transcribes a meeting, extracts what was committed to, and then executes it — ' +
      'but only after you approve. Nothing reaches Calendar, Notion, Gmail, or Slack on its own.',
    points: [
      'Every action carries an owner, a deadline, and a priority',
      'Each one records the timestamp and the exact quote it came from',
      'You can judge an item without replaying the meeting',
    ],
  },
  {
    icon: 'bi-list-check',
    title: 'Three groups, one decision each',
    body: 'The action items board sorts everything into what you can act on now and what needs a look.',
    points: [
      'Ready to Execute — complete, owned, passing every guardrail',
      'Needs Clarification — a missing detail, low confidence, or a blocked rule',
      'Informational — recorded for the record, nothing to execute',
    ],
  },
  {
    icon: 'bi-shield-exclamation',
    title: 'Risk decides how much ceremony',
    body:
      'Each action is classified by what it would actually do in the world, and the classification ' +
      'is recomputed on the server every time — the browser cannot talk its way past it.',
    points: [
      'Low — reversible and private to you, like a draft or a note',
      'Medium — visible to colleagues, so it needs your explicit approval',
      'High — reaches outside the org or destroys data: you type EXECUTE to confirm',
    ],
  },
  {
    icon: 'bi-shield-check',
    title: 'You are in mock mode',
    body:
      'Executions are simulated end to end and labelled "simulated", so you can walk the whole ' +
      'approval path safely before connecting a single account. Switch to live only when ready.',
    points: [
      'Every action type works without any credentials',
      'Guardrails, retries, and the audit trail all behave exactly as they will live',
      'The banner at the top of every page tells you which mode you are in',
    ],
    tone: 'caution',
  },
  {
    icon: 'bi-key',
    title: 'Bring your own keys',
    body:
      'Add API keys for transcription, extraction, translation, or search under Settings → API keys. ' +
      '31 providers are supported and 14 of them are free or run entirely on your machine.',
    points: [
      'Free tiers are listed first, with their actual allowances',
      'Keys are encrypted at rest and never shown again after saving',
      'Leave a module blank and it stays mocked rather than failing',
    ],
  },
  {
    icon: 'bi-journal-text',
    title: 'Everything is on the record',
    body:
      'The audit log keeps an append-only trail of what was proposed, what you decided, and what ' +
      'happened — including the exact payload sent and any rule that blocked an attempt.',
    points: [
      'No update or delete path exists anywhere in the codebase',
      'Your edits are also stored as corrections, to improve future extraction',
      'Start from the Dashboard to see usage, the workflow funnel, and guardrail activity',
    ],
  },
]

export function OnboardingTour() {
  const [index, setIndex] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const [saving, setSaving] = useState(false)
  const dialogRef = useRef<HTMLDivElement>(null)

  const finish = useCallback(
    async (action: 'complete' | 'skip', reachedStep: number) => {
      setSaving(true)
      // Close immediately: making someone wait on a network round-trip to leave a
      // tour is the opposite of what a skip button is for. The write is best-effort
      // and, if it fails, the tour reappears next session rather than losing state.
      setDismissed(true)
      try {
        await apiFetch('/api/profile/onboarding', {
          method: 'POST',
          body: JSON.stringify({ action, reachedStep }),
        })
      } catch {
        /* best effort */
      } finally {
        setSaving(false)
      }
    },
    [],
  )

  const step = STEPS[index]!
  const isLast = index === STEPS.length - 1

  const next = useCallback(() => {
    if (isLast) void finish('complete', index)
    else setIndex((i) => i + 1)
  }, [isLast, index, finish])

  const back = useCallback(() => setIndex((i) => Math.max(0, i - 1)), [])

  useEffect(() => {
    if (dismissed) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') void finish('skip', index)
      else if (event.key === 'ArrowRight') next()
      else if (event.key === 'ArrowLeft') back()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dismissed, index, next, back, finish])

  // Move focus into the dialog so a screen reader announces it and the arrow keys
  // are immediately meaningful.
  useEffect(() => {
    if (!dismissed) dialogRef.current?.focus()
  }, [dismissed, index])

  useEffect(() => {
    if (dismissed) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [dismissed])

  if (dismissed) return null

  return (
    <div className="fixed inset-0 z-[70] grid place-items-center p-4">
      <div className="absolute inset-0 bg-abyss/80 backdrop-blur-sm" aria-hidden />

      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        tabIndex={-1}
        className="panel-strong lit-edge relative w-full max-w-lg animate-fade-up rounded-2xl outline-none"
      >
        <div className="flex items-start gap-3 px-5 pt-5">
          <span
            className={clsx(
              'grid size-10 shrink-0 place-items-center rounded-xl',
              step.tone === 'caution' ? 'bg-ok/15 text-ok' : 'bg-accent-fill text-accent-on',
            )}
          >
            <i className={clsx('bi', step.icon, 'text-lg')} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="mono-num text-ink-faint">
              Step {index + 1} of {STEPS.length}
            </p>
            <h2 id="tour-title" className="mt-0.5 text-lg font-semibold leading-snug">
              {step.title}
            </h2>
          </div>
          <button
            onClick={() => void finish('skip', index)}
            className="rounded-lg p-1 text-ink-faint hover:bg-ink/5 hover:text-ink"
            aria-label="Skip the tour"
          >
            <i className="bi bi-x-lg text-sm" aria-hidden />
          </button>
        </div>

        <div className="px-5 pb-2 pt-3">
          <p className="text-sm leading-relaxed text-ink-muted">{step.body}</p>
          {step.points && (
            <ul className="mt-3 space-y-1.5">
              {step.points.map((point) => (
                <li key={point} className="flex gap-2.5 text-sm text-ink-muted">
                  <i className="bi bi-check2 mt-0.5 shrink-0 text-accent" aria-hidden />
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex items-center gap-3 border-t border-edge/20 px-5 py-3.5">
          {/* Progress dots double as direct navigation — a six-step tour should not
              force you to click Next four times to re-read step five. */}
          <div className="flex items-center gap-1.5" role="tablist" aria-label="Tour progress">
            {STEPS.map((s, i) => (
              <button
                key={s.title}
                onClick={() => setIndex(i)}
                role="tab"
                aria-selected={i === index}
                aria-label={`Step ${i + 1}: ${s.title}`}
                className={clsx(
                  'h-1.5 rounded-full transition-all',
                  i === index ? 'w-5 bg-accent-fill' : 'w-1.5 bg-ink-faint/40 hover:bg-ink-faint',
                )}
              />
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <Button variant="ghost" onClick={() => void finish('skip', index)} disabled={saving}>
              Skip tour
            </Button>
            {index > 0 && (
              <Button variant="secondary" icon="bi-arrow-left" onClick={back}>
                Back
              </Button>
            )}
            <Button
              variant="primary"
              icon={isLast ? 'bi-check-lg' : 'bi-arrow-right'}
              onClick={next}
              loading={saving && isLast}
            >
              {isLast ? 'Finish' : 'Next'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
