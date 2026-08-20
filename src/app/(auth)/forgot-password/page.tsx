import type { Metadata } from 'next'
import { ForgotPasswordForm } from '@/components/auth/ForgotPasswordForm'
import { deliveryConfigured } from '@/server/email/mailer'
import { env } from '@/lib/env'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title: 'Reset your password — Voice2BRD',
  robots: { index: false, follow: false },
}

export default function ForgotPasswordPage() {
  return (
    <ForgotPasswordForm
      // When no mail provider is configured outside production, the API hands back
      // the link so a self-hosted or local flow can continue without an inbox. The
      // form only offers to show it because of this flag.
      canShowDevLink={!deliveryConfigured() && env().DEV_EXPOSE_RESET_LINK}
    />
  )
}
