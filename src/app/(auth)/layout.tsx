import Link from 'next/link'
import { redirect } from 'next/navigation'
import { ThemeToggle } from '@/components/ui/ThemeToggle'
import { optionalUser } from '@/lib/auth'

/**
 * Shell for the login and sign-up screens. A route group rather than a nested
 * path, so these pages sit at `/login` and `/signup` without inheriting the
 * dashboard chrome — and without the dashboard's own user lookup, which would
 * bounce an unauthenticated visitor away from the very page they need.
 */
export default async function AuthLayout({ children }: { children: React.ReactNode }) {
  // Someone with a real session has no business on a login form. The development
  // identity deliberately does *not* trigger this: otherwise `/login` would be
  // unreachable locally, which makes it impossible to work on.
  const user = await optionalUser()
  if (user?.source === 'session') redirect('/dashboard')

  return (
    <div className="relative flex min-h-dvh flex-col">
      <header className="flex items-center gap-3 px-4 py-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="glow-accent grid size-8 place-items-center rounded-xl bg-accent-fill text-accent-on">
            <i className="bi bi-soundwave text-base" aria-hidden />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">Vowcraft</span>
        </Link>
        <div className="ml-auto">
          <ThemeToggle compact />
        </div>
      </header>

      <main className="grid flex-1 place-items-center px-4 pb-16 pt-2 sm:px-6">
        <div className="w-full max-w-md">{children}</div>
      </main>
    </div>
  )
}
