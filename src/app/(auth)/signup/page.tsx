import type { Metadata } from 'next'
import { AuthForm } from '@/components/auth/AuthForm'
import { sessionsAvailable } from '@/lib/session'
import { googleConfigured } from '@/server/auth/google'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Create an account — Vowcraft',
  robots: { index: false, follow: false },
}

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>
}) {
  const { next, error } = await searchParams
  const safeNext = next && next.startsWith('/') && !next.startsWith('//') ? next : undefined

  return (
    <AuthForm
      mode="signup"
      next={safeNext}
      sessionsAvailable={sessionsAvailable()}
      googleAvailable={googleConfigured()}
      // Carried back by the Google callback when a federated attempt failed, so the
      // reason lands on the form rather than on an error boundary.
      initialError={error}
    />
  )
}
