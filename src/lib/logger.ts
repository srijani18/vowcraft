/**
 * One-line structured JSON logs correlated by requestId (SPEC-000 §6).
 *
 * Every value passes through `redact()` before serialisation. The denylist is a
 * second line of defence, not the primary one — secrets are not supposed to
 * reach a log call at all (SPEC-004 §5) — but defence in depth is cheap here and
 * the failure it prevents is unrecoverable.
 */

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 } as const
export type Level = keyof typeof LEVELS

const SENSITIVE = /(secret|token|password|api[-_]?key|authorization|cookie|refresh|credential|bearer)/i

/*
 * Key-shaped substrings, masked wherever they appear in a *value*.
 *
 * The key-name denylist above cannot help when a secret arrives inside otherwise
 * innocuous text — a provider error body echoing the request, a stack frame carrying a
 * URL, a message built by string concatenation. Provider keys are long, prefixed and
 * unmistakable, which makes them cheap to spot and safe to mask: nothing else in a log
 * line looks like `gsk_` followed by forty base62 characters.
 */
const KEY_SHAPED: readonly RegExp[] = [
  /\b(gsk_|sk-proj-|sk-ant-|sk-|csk-|xoxb-|xoxp-|secret_|nvapi-|r8_)[A-Za-z0-9_-]{16,}/g, // OpenAI-style prefixes
  /\bAIza[A-Za-z0-9_-]{30,}/g, // Google
  /\bBearer\s+[A-Za-z0-9._~+/=-]{16,}/gi, // any Authorization value that slipped into text
  /\bkey=[A-Za-z0-9._~+/=-]{16,}/g, // Gemini passes the key in the query string
]

function scrub(text: string): string {
  let out = text
  for (const pattern of KEY_SHAPED) {
    out = out.replace(pattern, (match) => {
      // Keep the prefix so the log still says *which* provider, never the secret.
      const prefix = /^(Bearer\s+|key=)/i.exec(match)?.[1] ?? /^[A-Za-z_]*[_-]/.exec(match)?.[0] ?? ''
      return `${prefix}[redacted]`
    })
  }
  return out
}

function threshold(): number {
  const configured = (process.env.LOG_LEVEL ?? 'info') as Level
  return LEVELS[configured] ?? LEVELS.info
}

function redact(value: unknown, depth = 0): unknown {
  if (depth > 6) return '[depth]'
  if (value === null || value === undefined) return value
  if (value instanceof Error) {
    return {
      name: value.name,
      message: scrub(value.message),
      stack: value.stack?.split('\n').slice(0, 4).map(scrub),
    }
  }
  if (Array.isArray(value)) return value.map((v) => redact(v, depth + 1))
  if (typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = SENSITIVE.test(k) ? '[redacted]' : redact(v, depth + 1)
    }
    return out
  }
  if (typeof value === 'string') return scrub(value)
  return value
}

function emit(level: Level, event: string, fields: Record<string, unknown> = {}) {
  if (LEVELS[level] < threshold()) return
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    level,
    event,
    ...(redact(fields) as Record<string, unknown>),
  })
  if (level === 'error') console.error(line)
  else if (level === 'warn') console.warn(line)
  else console.log(line)
}

export interface Logger {
  debug(event: string, fields?: Record<string, unknown>): void
  info(event: string, fields?: Record<string, unknown>): void
  warn(event: string, fields?: Record<string, unknown>): void
  error(event: string, fields?: Record<string, unknown>): void
  child(bound: Record<string, unknown>): Logger
}

export function createLogger(bound: Record<string, unknown> = {}): Logger {
  return {
    debug: (e, f) => emit('debug', e, { ...bound, ...f }),
    info: (e, f) => emit('info', e, { ...bound, ...f }),
    warn: (e, f) => emit('warn', e, { ...bound, ...f }),
    error: (e, f) => emit('error', e, { ...bound, ...f }),
    child: (extra) => createLogger({ ...bound, ...extra }),
  }
}

export const logger = createLogger({ service: 'web' })
