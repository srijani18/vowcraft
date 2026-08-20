import { CredentialsManager } from '@/components/credentials/CredentialsManager'
import { apiServerJson } from '@/lib/api-server'
import type { CredentialView, ModuleAvailability } from '@/lib/credentials/types'

export const dynamic = 'force-dynamic'

interface CredentialsResponse {
  credentials: CredentialView[]
  modules: ModuleAvailability[]
  encryptionConfigured: boolean
}

/** `/dashboard/settings/credentials` — SPEC-004 §9. */
export default async function CredentialsPage() {
  const { credentials, modules, encryptionConfigured } =
    await apiServerJson<CredentialsResponse>('/api/credentials')

  return (
    <CredentialsManager
      initial={credentials}
      initialModules={modules}
      encryptionConfigured={encryptionConfigured}
    />
  )
}
