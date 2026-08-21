'use client'

import clsx from 'clsx'
import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { useEffect, useState } from 'react'
import { NAV_GROUPS, isActive, type NavGroup } from '@/lib/navigation'
import { ThemeToggle } from './ThemeToggle'
import { clearTokens } from '@/lib/api-client'

/**
 * Module navigation — SPEC-005 §2.
 *
 * One component serves both layouts: a persistent rail on desktop and a drawer
 * behind a hamburger on mobile. Groups collapse, and the group containing the
 * current route is expanded on load so the user is never looking at a closed
 * accordion that hides where they are.
 *
 * `planned` modules stay visible and route to a page describing the feature. A
 * nav that hides unfinished work makes the tool look smaller than it is; one that
 * links to 404s makes it look broken. Labelling them is the honest third option.
 */

export function Sidebar({
  userName,
  userEmail,
}: {
  userName: string | null
  userEmail: string
}) {
  const pathname = usePathname()
  const search = useSearchParams().toString()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  // Close the drawer on navigation — otherwise it covers the page you just chose.
  useEffect(() => setDrawerOpen(false), [pathname])

  // Lock body scroll behind the drawer so the page underneath does not move.
  useEffect(() => {
    if (!drawerOpen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [drawerOpen])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawerOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const activeGroup = NAV_GROUPS.find((g) => g.modules.some((m) => isActive(m, pathname, search)))

  const isGroupOpen = (group: NavGroup) =>
    collapsed[group.id] === undefined ? group.id === activeGroup?.id || group.id === 'overview' : !collapsed[group.id]

  const toggleGroup = (group: NavGroup) =>
    setCollapsed((prev) => ({ ...prev, [group.id]: isGroupOpen(group) }))

  const nav = (
    <nav className="flex h-full flex-col gap-1 overflow-y-auto px-3 pb-4" aria-label="Modules">
      {NAV_GROUPS.map((group) => {
        const open = isGroupOpen(group)
        return (
          <div key={group.id} className="mb-1">
            <button
              onClick={() => toggleGroup(group)}
              aria-expanded={open}
              aria-controls={`nav-group-${group.id}`}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-[11px] font-semibold uppercase tracking-wide text-ink-faint transition-colors hover:bg-ink/5 hover:text-ink-muted"
            >
              <i className={clsx('bi', group.icon)} aria-hidden />
              <span className="flex-1 text-left">{group.label}</span>
              <i
                className={clsx('bi bi-chevron-down text-[10px] transition-transform', !open && '-rotate-90')}
                aria-hidden
              />
            </button>

            {open && (
              <ul id={`nav-group-${group.id}`} className="mt-0.5 space-y-0.5 pl-1">
                {group.modules.map((module) => {
                  const active = isActive(module, pathname, search)
                  return (
                    <li key={module.slug}>
                      <Link
                        href={module.href}
                        title={module.blurb}
                        aria-current={active ? 'page' : undefined}
                        className={clsx(
                          'group flex items-center gap-2.5 rounded-xl px-2.5 py-2 text-sm transition-colors',
                          active
                            ? 'bg-accent-fill/15 font-medium text-accent shadow-[inset_2px_0_0_0_rgb(var(--accent-fill))]'
                            : 'text-ink-muted hover:bg-ink/5 hover:text-ink',
                        )}
                      >
                        <i className={clsx('bi', module.icon, 'shrink-0')} aria-hidden />
                        <span className="flex-1 truncate">{module.label}</span>
                        {module.status === 'planned' && (
                          <span
                            className="shrink-0 rounded border border-edge/30 px-1 text-[9px] font-medium uppercase tracking-wide text-ink-faint"
                            title="Specified, not yet built"
                          >
                            soon
                          </span>
                        )}
                      </Link>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        )
      })}
    </nav>
  )

  return (
    <>
      {/* ── mobile header with the hamburger */}
      <header className="sticky top-0 z-40 flex items-center gap-3 border-b border-edge/20 bg-base/90 px-4 py-3 backdrop-blur-xl lg:hidden">
        <button
          onClick={() => setDrawerOpen(true)}
          aria-label="Open modules menu"
          aria-expanded={drawerOpen}
          className="grid size-9 place-items-center rounded-xl border border-edge/30 bg-surface-strong/70 text-ink"
        >
          <i className="bi bi-list text-lg" aria-hidden />
        </button>
        <Brand />
        <div className="ml-auto">
          <ThemeToggle compact />
        </div>
      </header>

      {/* ── mobile drawer */}
      {drawerOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            className="absolute inset-0 bg-abyss/70 backdrop-blur-sm"
            onClick={() => setDrawerOpen(false)}
            aria-label="Close modules menu"
            tabIndex={-1}
          />
          <div className="panel-strong absolute inset-y-0 left-0 flex w-[min(19rem,85vw)] animate-fade-up flex-col rounded-r-2xl">
            <div className="flex items-center gap-2 px-4 py-4">
              <Brand />
              <button
                onClick={() => setDrawerOpen(false)}
                aria-label="Close modules menu"
                className="ml-auto grid size-8 place-items-center rounded-lg text-ink-faint hover:bg-ink/5 hover:text-ink"
              >
                <i className="bi bi-x-lg text-sm" aria-hidden />
              </button>
            </div>
            {nav}
            <AccountFooter userName={userName} userEmail={userEmail} />
          </div>
        </div>
      )}

      {/* ── desktop rail */}
      <aside className="hidden shrink-0 border-r border-edge/20 lg:sticky lg:top-0 lg:flex lg:h-dvh lg:w-64 lg:flex-col">
        <div className="flex items-center gap-2 px-4 py-5">
          <Brand />
          <div className="ml-auto">
            <ThemeToggle compact />
          </div>
        </div>
        {nav}
        <AccountFooter userName={userName} userEmail={userEmail} />
      </aside>
    </>
  )
}

function Brand() {
  return (
    <Link href="/dashboard" className="flex items-center gap-2.5">
      <span className="grid size-8 place-items-center rounded-xl bg-accent-fill text-accent-on glow-accent">
        <i className="bi bi-soundwave text-base" aria-hidden />
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold text-ink">Vowcraft</span>
        <span className="block text-[10px] uppercase tracking-wider text-ink-faint">voice → action</span>
      </span>
    </Link>
  )
}

function AccountFooter({
  userName,
  userEmail,
}: {
  userName: string | null
  userEmail: string
}) {
  return (
    <div className="mt-auto border-t border-edge/20 px-3 py-3">
      <Link
        href="/dashboard/settings/profile"
        className="flex items-center gap-2.5 rounded-xl px-2 py-2 transition-colors hover:bg-ink/5"
      >
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-mid/15 text-xs font-semibold text-accent">
          {(userName ?? userEmail).slice(0, 2).toUpperCase()}
        </span>
        <span className="min-w-0 leading-tight">
          <span className="block truncate text-xs font-medium text-ink">{userName ?? 'Your account'}</span>
          <span className="block truncate text-[10px] text-ink-faint">{userEmail}</span>
        </span>
        <i className="bi bi-gear ml-auto shrink-0 text-xs text-ink-faint" aria-hidden />
      </Link>

      <button
        onClick={async () => {
          await fetch('/api/auth/logout', { method: 'POST' })
          // Drop the backend token as well. Leaving it behind would let the next person
          // on a shared machine keep calling the backend as the previous user.
          clearTokens()
          // Full navigation so every server component re-renders without the session.
          window.location.assign('/login')
        }}
        className="mt-0.5 flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-left text-xs text-ink-muted transition-colors hover:bg-ink/5 hover:text-ink"
      >
        <span className="grid size-8 shrink-0 place-items-center">
          <i className="bi bi-box-arrow-right text-sm" aria-hidden />
        </span>
        Sign out
      </button>
    </div>
  )
}
