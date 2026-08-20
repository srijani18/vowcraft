import { redirect } from 'next/navigation'
import { Sidebar } from '@/components/ui/Sidebar'
import { OnboardingTour } from '@/components/onboarding/OnboardingTour'
import { optionalUser } from '@/lib/auth'
import { db } from '@/lib/db'
import { env } from '@/lib/env'

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  // Redirect rather than throw: an expired session should land on the login form,
  // not an error boundary. The dev identity still resolves here in development, so
  // `docker compose up` and the smoke suite need no login step.
  const user = await optionalUser()
  if (!user) redirect('/login?next=/dashboard')
  const profile = await db.user.findUnique({
    where: { id: user.id },
    select: { name: true, email: true, onboardingCompletedAt: true, deletionRequestedAt: true },
  })

  const mode = env().INTEGRATIONS_MODE
  const showTour = profile !== null && profile.onboardingCompletedAt === null
  // A resolved-but-not-signed-in identity must be visible. Silently showing someone
  // else's account to a user who just signed in is worse than showing them nothing.
  const usingDevIdentity = user.source === 'dev'

  return (
    <div className="flex min-h-dvh flex-col lg:flex-row">
      <Sidebar userName={profile?.name ?? user.name} userEmail={profile?.email ?? user.email} />

      <div className="min-w-0 flex-1">
        {/* Mode is surfaced in the chrome rather than buried in settings: a
            reviewer must never be unsure whether a click reaches the real world. */}
        <div
          className={
            mode === 'mock'
              ? 'border-b border-edge/20 bg-ok/10 px-4 py-1.5 text-center text-[11px] text-ok sm:px-6'
              : 'border-b border-edge/20 bg-warn/12 px-4 py-1.5 text-center text-[11px] text-warn sm:px-6'
          }
        >
          <i className={`bi ${mode === 'mock' ? 'bi-shield-check' : 'bi-broadcast'} mr-1.5`} aria-hidden />
          {mode === 'mock'
            ? 'Mock mode — executions are simulated and nothing leaves this machine.'
            : 'Live mode — executions reach real third-party accounts.'}
        </div>

        {usingDevIdentity && (
          <div className="border-b border-warn/40 bg-warn/12 px-4 py-2 text-center text-xs text-warn sm:px-6">
            <i className="bi bi-person-badge mr-1.5" aria-hidden />
            You are not signed in — this is the local development identity (
            <span className="font-mono">{user.email}</span>).{' '}
            <a href="/login" className="font-semibold underline">
              Sign in
            </a>{' '}
            to see your own account.
          </div>
        )}

        {profile?.deletionRequestedAt && (
          <div className="border-b border-danger/30 bg-danger/12 px-4 py-2 text-center text-xs text-danger sm:px-6">
            <i className="bi bi-exclamation-octagon-fill mr-1.5" aria-hidden />
            This account is scheduled for deletion.{' '}
            <a href="/dashboard/settings/profile" className="font-semibold underline">
              Cancel it
            </a>
          </div>
        )}

        <main id="main" className="mx-auto w-full max-w-[1500px] px-4 pb-16 pt-5 sm:px-6 lg:px-8 lg:pt-8">
          {children}
        </main>
      </div>

      {showTour && <OnboardingTour />}
    </div>
  )
}
