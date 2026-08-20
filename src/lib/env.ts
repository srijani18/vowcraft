import { z } from 'zod'

/**
 * Environment contract. Parsed once, lazily, so a missing optional key is a
 * degraded feature (SPEC-004 §7) rather than a boot crash — but a malformed
 * required key fails fast and loudly.
 */
const schema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  DATABASE_URL: z.string().min(1, 'DATABASE_URL is required'),
  APP_URL: z.string().url().default('http://localhost:3000'),

  /** 32 random bytes, base64. Generate: openssl rand -base64 32 */
  APP_ENCRYPTION_KEY: z.string().min(1).optional(),

  /** SPEC-002 §2 — mock is the default so an unconfigured app cannot email anyone. */
  INTEGRATIONS_MODE: z.enum(['mock', 'live']).default('mock'),
  /** Comma-separated provider ids forced live inside an otherwise-mock process. */
  INTEGRATIONS_LIVE: z.string().default(''),

  /** SPEC-004 §3 — invert precedence so env wins over user-supplied keys. */
  CREDENTIALS_ENV_LOCKED: z
    .string()
    .default('false')
    .transform((v) => v === 'true'),

  LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),

  /**
   * The public marketing site. Present ⇒ the app shows a link back to it. Absent ⇒
   * the link is hidden rather than pointing nowhere, since the two are separate
   * deployments and either can exist without the other.
   */
  NEXT_PUBLIC_MARKETING_URL: z.string().url().optional(),



  /**
   * Return the password-reset link in the API response when no mail provider is
   * configured — SPEC-007 §2.5.
   *
   * An explicit opt-in rather than a `NODE_ENV !== 'production'` guess, because the
   * standard local stack *is* a production build in a container: guessing would make
   * the reset flow uncompletable in exactly the place it most needs demonstrating.
   * Defaults to false, and `docker-compose.yml` turns it on because that file is
   * unambiguously the local-testing stack.
   */
  DEV_EXPOSE_RESET_LINK: z
    .string()
    .default('false')
    .transform((v) => v === 'true'),

  // Dev-only identity seam until NextAuth lands (SPEC-000 §7).
  DEV_USER_EMAIL: z.string().email().default('demo@vowcraft.test'),

  // OAuth apps — optional; absence means the provider shows as unconfigured.
  GOOGLE_CLIENT_ID: z.string().optional(),
  GOOGLE_CLIENT_SECRET: z.string().optional(),
  NOTION_CLIENT_ID: z.string().optional(),
  NOTION_CLIENT_SECRET: z.string().optional(),
  SLACK_CLIENT_ID: z.string().optional(),
  SLACK_CLIENT_SECRET: z.string().optional(),

  /**
   * Module API keys are NOT declared here. They are the ENV tier of the
   * credential vault (SPEC-004 §3) and are read through the catalogue by name,
   * so adding a provider stays a one-line change in `credentials/catalog.ts`
   * rather than an edit in two places. `.env.example` documents them all.
   *
   * The two exceptions below are read directly by adapter code.
   */
  SLACK_BOT_TOKEN: z.string().optional(),
  NOTION_API_KEY: z.string().optional(),
})

export type Env = z.infer<typeof schema>

let cached: Env | null = null

export function env(): Env {
  if (cached) return cached
  const parsed = schema.safeParse(process.env)
  if (!parsed.success) {
    const detail = parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; ')
    throw new Error(`Invalid environment: ${detail}`)
  }
  cached = parsed.data
  return cached
}

/** Test seam — lets a suite swap the environment without reloading modules. */
export function __setEnvForTests(value: Env | null) {
  cached = value
}

export function isProviderLive(providerId: string): boolean {
  const e = env()
  if (e.INTEGRATIONS_MODE === 'live') return true
  return e.INTEGRATIONS_LIVE.split(',')
    .map((s) => s.trim())
    .filter(Boolean)
    .includes(providerId)
}
