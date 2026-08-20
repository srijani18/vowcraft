import type { Metadata } from 'next'
import { AuthForm } from '@/components/auth/AuthForm'
import { sessionsAvailable } from '@/lib/session'
import { googleConfigured } from '@/server/auth/google'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Log in — Voice2BRD',
  robots: { index: false, follow: false },
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>
}) {
  const { next, error } = await searchParams
  // Only same-origin paths are honoured: accepting an absolute URL here would make
  // the login page an open redirect, which is how phishing links get their polish.
  const safeNext = next && next.startsWith('/') && !next.startsWith('//') ? next : undefined

  return (
    <AuthForm
      mode="login"
      next={safeNext}
      sessionsAvailable={sessionsAvailable()}
      googleAvailable={googleConfigured()}
      // Carried back by the Google callback when a federated attempt failed, so the
      // reason lands on the form rather than on an error boundary.
      initialError={error}
    />
  )
}
