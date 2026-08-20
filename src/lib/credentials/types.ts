export type CredentialModule =
  | 'TRANSCRIPTION'
  | 'EXTRACTION'
  | 'TRANSLATION'
  | 'EMBEDDING'
  | 'INTEGRATION'

export type CredentialStatus = 'UNVERIFIED' | 'VALID' | 'INVALID'

/** Which tier of SPEC-004 §3 satisfied the lookup. */
export type CredentialSource = 'USER' | 'ENV' | 'NONE'

/** The only credential shape allowed to cross the API boundary — SPEC-004 §5. */
export type PricingTier = 'free' | 'freemium' | 'paid' | 'local'

export interface CredentialView {
  service: string
  displayName: string
  module: CredentialModule
  blurb: string
  docsUrl: string
  tier: PricingTier
  /** The concrete allowance, e.g. "14,400 requests/day". */
  costNote: string
  models: string[]
  localOnly: boolean
  source: CredentialSource
  configured: boolean
  enabled: boolean
  status: CredentialStatus
  lastVerifiedAt: string | null
  lastError: string | null
  /** Masked previews keyed by field, e.g. `{ apiKey: "sk-pr…9f2a" }`. */
  hints: Record<string, string>
  fields: {
    key: string
    label: string
    placeholder: string
    secret: boolean
    required: boolean
    help?: string
  }[]
  canVerify: boolean
  /** Set when this service is satisfied by a sibling's key rather than its own. */
  sharedFrom: string | null
  /** Other services this key also unlocks, shown where the key is entered. */
  alsoUsedBy: string[]
}

export interface ModuleAvailability {
  module: CredentialModule
  title: string
  blurb: string
  icon: string
  /** live: a real key is present. mocked: no key, mock mode covers it. */
  state: 'live' | 'mocked' | 'unavailable'
  activeService: string | null
  source: CredentialSource
}
