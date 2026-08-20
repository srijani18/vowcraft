import { redirect } from 'next/navigation'
import { optionalUser } from '@/lib/auth'

export const dynamic = 'force-dynamic'

export default async function Home() {
  const user = await optionalUser()
  redirect(user ? '/dashboard' : '/login')
}
