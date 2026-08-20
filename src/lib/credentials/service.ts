import { db } from '@/lib/db'
import { record } from '@/lib/audit'
import { decryptJson, encryptJson, encryptionAvailable, maskSecret } from '@/lib/crypto'
import { env } from '@/lib/env'
import { logger } from '@/lib/logger'
import { AppError, badRequest, notFound, unprocessable } from '@/lib/errors'
import { CATALOG, MODULE_META, MODULE_ORDER, findService, siblingsOf, type ServiceSpec } from './catalog'
import type {
  CredentialModule,
  CredentialSource,
  CredentialStatus,
  CredentialView,
  ModuleAvailability,
} from './types'

/**
 * Credential vault — SPEC-004.
 *
 * Plaintext secrets exist only inside this module. `toView()` is the single
 * function the API is allowed to serialise, and it emits masked hints only:
 * there is no read path for a stored secret, by design.
 */

type Secrets = Record<string, string>

function aad(userId: string, service: string): string {
  // Binds the ciphertext to its owner and service (SPEC-004 §4). A row copied
  // elsewhere fails authentication rather than quietly decrypting.
  return `cred:${userId}:${service}`
}

function envValue(spec: ServiceSpec): string | undefined {
  if (!spec.envVar) return undefined
  const value = (process.env as Record<string, string | undefined>)[spec.envVar]
  return value && value.trim() ? value.trim() : undefined
}

// ────────────────────────────────────────────────────────── resolution ──

export interface ResolvedCredential {
  service: string
  source: CredentialSource
  secrets: Secrets
}

/**
 * SPEC-004 §3. A user key wins over the environment so "use my own quota" is
 * expressible on a shared deployment; `CREDENTIALS_ENV_LOCKED` inverts that for
 * a locked-down install.
 */
export async function resolveCredential(
  userId: string,
  service: string,
  seen: Set<string> = new Set(),
): Promise<ResolvedCredential> {
  const spec = findService(service)
  if (!spec) throw notFound(`Unknown service “${service}”.`)
  if (spec.localOnly) return { service, source: 'ENV', secrets: {} }

  const fromEnv = envValue(spec)
  const envFirst = env().CREDENTIALS_ENV_LOCKED

  if (envFirst && fromEnv) {
    return { service, source: 'ENV', secrets: envSecrets(spec, fromEnv) }
  }

  const row = await db.credential.findUnique({ where: { userId_service: { userId, service } } })
  if (row && row.enabled && row.status !== 'INVALID') {
    try {
      return { service, source: 'USER', secrets: decryptJson<Secrets>(row.secretsEnc, aad(userId, service)) }
    } catch (err) {
      // A row we cannot decrypt is a rotated or wrong APP_ENCRYPTION_KEY. Fall
      // back to the environment rather than failing the whole request, and say so.
      logger.error('credential.decrypt_failed', { service, userId, err })
    }
  }

  if (fromEnv) return { service, source: 'ENV', secrets: envSecrets(spec, fromEnv) }

  /*
   * Fall through to the service this one shares a key with. One Groq account covers
   * both transcription and extraction, and one OpenAI key covers audio, chat and
   * embeddings — so a key entered once must work for every capability it actually
   * unlocks. Without this, someone who added a Groq key under "Transcription" got "no
   * extraction provider is configured" and no indication why.
   *
   * `seen` guards against a mis-authored catalogue pointing two entries at each other.
   */
  if (spec.sharesKeyWith && !seen.has(spec.sharesKeyWith)) {
    const shared = await resolveCredential(userId, spec.sharesKeyWith, new Set([...seen, service]))
    if (shared.source !== 'NONE') {
      // Reported under the service that was asked for, so callers and logs stay
      // meaningful, but the secrets came from the sibling.
      return { service, source: shared.source, secrets: shared.secrets }
    }
  }

  return { service, source: 'NONE', secrets: {} }
}

/** Maps a single env var onto the spec's field shape. */
function envSecrets(spec: ServiceSpec, value: string): Secrets {
  const primary = spec.fields.find((f) => f.required) ?? spec.fields[0]
  if (!primary) return {}
  const secrets: Secrets = { [primary.key]: value }
  // A multi-field service reads its remaining fields from sibling env vars.
  for (const field of spec.fields) {
    if (field.key === primary.key) continue
    const sibling = (process.env as Record<string, string | undefined>)[
      `${spec.service.toUpperCase()}_${field.key.replace(/([A-Z])/g, '_$1').toUpperCase()}`
    ]
    if (sibling?.trim()) secrets[field.key] = sibling.trim()
  }
  return secrets
}

/** First service in a module that resolves to a usable key. */
export async function resolveModule(
  userId: string,
  module: CredentialModule,
): Promise<ResolvedCredential | null> {
  for (const spec of CATALOG.filter((s) => s.module === module)) {
    const resolved = await resolveCredential(userId, spec.service)
    if (resolved.source !== 'NONE' || spec.localOnly) return resolved
  }
  return null
}

// ──────────────────────────────────────────────────────────── read API ──

export async function listCredentials(userId: string): Promise<CredentialView[]> {
  const rows = await db.credential.findMany({ where: { userId } })
  const byService = new Map(rows.map((r) => [r.service, r]))
  const envLocked = env().CREDENTIALS_ENV_LOCKED

  /** A service is also satisfied by whatever its shared sibling has. */
  const satisfiedBy = (spec: ServiceSpec): { via: string; source: CredentialSource } | null => {
    if (!spec.sharesKeyWith) return null
    const sibling = CATALOG.find((s) => s.service === spec.sharesKeyWith)
    if (!sibling) return null
    const siblingRow = byService.get(sibling.service)
    if (siblingRow?.enabled && siblingRow.status !== 'INVALID') {
      try {
        decryptJson<Secrets>(siblingRow.secretsEnc, aad(userId, sibling.service))
        return { via: sibling.displayName, source: 'USER' }
      } catch {
        /* an undecryptable sibling is no help */
      }
    }
    return envValue(sibling) ? { via: sibling.displayName, source: 'ENV' } : null
  }

  return CATALOG.map((spec) => {
    const row = byService.get(spec.service)
    const fromEnv = envValue(spec)
    const shared = satisfiedBy(spec)

    // A stored row only counts as usable if it actually decrypts. Reporting
    // "configured" for a row we cannot read would be a lie the UI acts on — it
    // happens whenever APP_ENCRYPTION_KEY is rotated or a row is moved between
    // accounts (the AAD bind then fails), and the honest answer is "re-enter it".
    let userUsable = false
    let decryptError: string | null = null
    if (row?.enabled && row.status !== 'INVALID') {
      try {
        decryptJson<Secrets>(row.secretsEnc, aad(userId, spec.service))
        userUsable = true
      } catch {
        decryptError =
          'The stored key could not be decrypted. APP_ENCRYPTION_KEY may have changed — re-enter the key.'
      }
    }

    const source: CredentialSource = spec.localOnly
      ? 'ENV'
      : envLocked && fromEnv
        ? 'ENV'
        : userUsable
          ? 'USER'
          : fromEnv
            ? 'ENV'
            : (shared?.source ?? 'NONE')

    return {
      service: spec.service,
      displayName: spec.displayName,
      module: spec.module,
      blurb: spec.blurb,
      docsUrl: spec.docsUrl,
      tier: spec.tier,
      costNote: spec.costNote,
      models: spec.models ?? [],
      localOnly: Boolean(spec.localOnly),
      source,
      configured: source !== 'NONE',
      enabled: row?.enabled ?? true,
      status: decryptError ? 'INVALID' : ((row?.status ?? 'UNVERIFIED') as CredentialStatus),
      lastVerifiedAt: row?.lastVerifiedAt?.toISOString() ?? null,
      lastError: decryptError ?? row?.lastError ?? null,
      hints: userUsable
        ? ((row?.hints as Record<string, string> | null) ?? {})
        : fromEnv
          ? { [primaryKey(spec)]: maskSecret(fromEnv) }
          : {},
      fields: spec.fields,
      canVerify: Boolean(spec.verify) && source !== 'NONE',
      // Set when this entry needs no key of its own because a sibling supplied one.
      sharedFrom: !userUsable && !fromEnv && shared ? shared.via : null,
      // Where this key is *also* used, so the UI can say so at the point of entry.
      alsoUsedBy: siblingsOf(spec.service)
        .filter((s) => s.sharesKeyWith === spec.service)
        .map((s) => s.displayName),
    } satisfies CredentialView
  })
}

function primaryKey(spec: ServiceSpec): string {
  return spec.fields.find((f) => f.required)?.key ?? spec.fields[0]?.key ?? 'apiKey'
}

/** Drives the persistent availability banner — SPEC-004 §7. */
export async function moduleAvailability(userId: string): Promise<ModuleAvailability[]> {
  const views = await listCredentials(userId)
  return MODULE_ORDER.map((module) => {
    const meta = MODULE_META[module]
    const configured = views.find((v) => v.module === module && v.configured)
    const mockCovers = module === 'INTEGRATION' && env().INTEGRATIONS_MODE === 'mock'

    return {
      module,
      title: meta.title,
      blurb: meta.blurb,
      icon: meta.icon,
      state: configured ? 'live' : mockCovers ? 'mocked' : 'unavailable',
      activeService: configured?.service ?? null,
      source: configured?.source ?? 'NONE',
    }
  })
}

// ─────────────────────────────────────────────────────────── write API ──

export async function saveCredential(
  userId: string,
  service: string,
  secrets: Secrets,
  options: { label?: string; enabled?: boolean; requestId?: string } = {},
): Promise<CredentialView> {
  const spec = findService(service)
  if (!spec) throw notFound(`Unknown service “${service}”.`)
  if (spec.localOnly) throw badRequest('local_only', `${spec.displayName} needs no API key.`)
  if (!encryptionAvailable()) {
    throw new AppError(
      503,
      'encryption_unavailable',
      'APP_ENCRYPTION_KEY is not configured, so secrets cannot be stored safely. ' +
        'Generate one with: openssl rand -base64 32',
    )
  }

  const cleaned: Secrets = {}
  for (const field of spec.fields) {
    const value = secrets[field.key]
    if (typeof value === 'string' && value.trim()) cleaned[field.key] = value.trim()
    else if (field.required) {
      throw unprocessable('missing_field', `${field.label} is required for ${spec.displayName}.`, {
        field: field.key,
      })
    }
  }

  const hints = Object.fromEntries(Object.entries(cleaned).map(([k, v]) => [k, maskSecret(v)]))
  const data = {
    module: spec.module,
    secretsEnc: encryptJson(cleaned, aad(userId, service)),
    hints,
    label: options.label ?? null,
    enabled: options.enabled ?? true,
    // A new value invalidates the previous verification result.
    status: 'UNVERIFIED' as const,
    lastError: null,
    lastVerifiedAt: null,
  }

  await db.credential.upsert({
    where: { userId_service: { userId, service } },
    create: { userId, service, ...data },
    update: data,
  })

  await record(db, {
    event: 'credential.saved',
    actorId: userId,
    requestId: options.requestId,
    // Field names only — never a value (SPEC-004 §9).
    metadata: { service, module: spec.module, fields: Object.keys(cleaned) },
  })

  const views = await listCredentials(userId)
  return views.find((v) => v.service === service)!
}

export async function deleteCredential(
  userId: string,
  service: string,
  requestId?: string,
): Promise<CredentialView> {
  const spec = findService(service)
  if (!spec) throw notFound(`Unknown service “${service}”.`)

  await db.credential.deleteMany({ where: { userId, service } })
  await record(db, {
    event: 'credential.deleted',
    actorId: userId,
    requestId,
    metadata: { service },
  })

  const views = await listCredentials(userId)
  return views.find((v) => v.service === service)!
}

// ───────────────────────────────────────────────────────── verification ──

const lastVerify = new Map<string, number>()
const VERIFY_COOLDOWN_MS = 10_000

/** SPEC-004 §8. Performs the catalogue's cheapest authenticated read. */
export async function verifyCredential(
  userId: string,
  service: string,
  requestId?: string,
): Promise<CredentialView> {
  const spec = findService(service)
  if (!spec) throw notFound(`Unknown service “${service}”.`)
  if (!spec.verify) throw badRequest('verify_unsupported', `${spec.displayName} has no verification endpoint.`)

  const throttleKey = `${userId}:${service}`
  const last = lastVerify.get(throttleKey) ?? 0
  if (Date.now() - last < VERIFY_COOLDOWN_MS) {
    throw new AppError(429, 'too_many_requests', 'Wait a few seconds before checking this key again.')
  }
  lastVerify.set(throttleKey, Date.now())

  const resolved = await resolveCredential(userId, service)
  const key = resolved.secrets[primaryKey(spec)]
  if (!key) throw unprocessable('not_configured', `No key stored for ${spec.displayName}.`)

  const recipe = spec.verify
  let url = recipe.url
  const headers: Record<string, string> = { ...(recipe.extraHeaders ?? {}) }

  if (recipe.auth === 'bearer') headers.authorization = `Bearer ${key}`
  else if (recipe.auth === 'header') headers[recipe.header ?? 'authorization'] = key
  else url += `${url.includes('?') ? '&' : '?'}key=${encodeURIComponent(key)}`

  let status: CredentialStatus = 'INVALID'
  let error: string | null = null

  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 10_000)
    const response = await fetch(url, {
      method: recipe.method ?? 'GET',
      headers,
      signal: controller.signal,
    }).finally(() => clearTimeout(timer))

    if (response.ok) {
      // Slack answers 200 with ok:false for a bad token, so a 2xx is not enough.
      const text = await response.text()
      status = /"ok"\s*:\s*false/.test(text) ? 'INVALID' : 'VALID'
      if (status === 'INVALID') error = 'The provider rejected this token.'
    } else {
      error = `${response.status} ${response.statusText}`.trim()
    }
  } catch (err) {
    error = (err as Error).name === 'AbortError' ? 'The provider did not respond in time.' : (err as Error).message
  }

  // Only a user-stored key has a row to stamp; an env key is the operator's.
  if (resolved.source === 'USER') {
    await db.credential.updateMany({
      where: { userId, service },
      data: { status, lastError: error, lastVerifiedAt: new Date() },
    })
  }

  await record(db, {
    event: 'credential.verified',
    actorId: userId,
    requestId,
    metadata: { service, status, source: resolved.source, error },
  })

  const views = await listCredentials(userId)
  const view = views.find((v) => v.service === service)!
  // Env-sourced keys have no row, so surface this run's result directly.
  return resolved.source === 'USER' ? view : { ...view, status, lastError: error }
}
