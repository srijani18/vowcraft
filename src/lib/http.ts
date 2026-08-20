import { NextResponse } from 'next/server'
import { randomUUID } from 'node:crypto'
import { ZodError } from 'zod'
import { UnauthenticatedError } from './auth'
import { AppError, badRequest } from './errors'
import { logger } from './logger'

/*
 * Re-exported so a caller that already imports `handle` does not need a second
 * import for the errors it throws. The definitions live in `errors.ts`, which has no
 * framework dependency — see the note there.
 */
export { AppError, badGateway, badRequest, conflict, notFound, unprocessable } from './errors'

/**
 * HTTP boundary helpers. Route handlers do three things only (SPEC-000 §3):
 * validate input, call a service, map the result. `handle()` owns the mapping so
 * no handler grows its own try/catch dialect.
 */

export function requestId(req: Request): string {
  return req.headers.get('x-request-id') ?? `req_${randomUUID().replace(/-/g, '').slice(0, 16)}`
}

export interface Ctx {
  requestId: string
  log: ReturnType<typeof logger.child>
}

/**
 * Wraps a handler: assigns a request id, logs the outcome, and converts thrown
 * errors into the documented envelope `{ error: { code, message, details? } }`.
 */
export function handle<T>(
  req: Request,
  fn: (ctx: Ctx) => Promise<T>,
): Promise<NextResponse> {
  const id = requestId(req)
  const log = logger.child({ requestId: id, method: req.method, path: new URL(req.url).pathname })
  const started = Date.now()

  return fn({ requestId: id, log })
    .then((body) => {
      log.info('request.ok', { durationMs: Date.now() - started })
      return NextResponse.json(body ?? { ok: true }, { headers: { 'x-request-id': id } })
    })
    .catch((err: unknown) => {
      const durationMs = Date.now() - started

      if (err instanceof ZodError) {
        log.warn('request.invalid', { durationMs, issues: err.issues })
        return NextResponse.json(
          {
            error: {
              code: 'invalid_request',
              message: 'Request validation failed.',
              details: err.issues.map((i) => ({ path: i.path.join('.'), message: i.message })),
            },
          },
          { status: 400, headers: { 'x-request-id': id } },
        )
      }

      // An expired or invalidated session is an expected outcome, not a fault. It
      // must be a 401 the client can act on, never a 500 that looks like a crash.
      if (err instanceof UnauthenticatedError) {
        log.info('request.unauthenticated', { durationMs })
        return NextResponse.json(
          { error: { code: 'unauthenticated', message: 'Sign in to continue.' } },
          { status: 401, headers: { 'x-request-id': id } },
        )
      }

      if (err instanceof AppError) {
        // 4xx is an expected outcome of a well-behaved system; 5xx is not.
        const level = err.status >= 500 ? 'error' : 'warn'
        log[level]('request.failed', { durationMs, status: err.status, code: err.code })
        return NextResponse.json(
          { error: { code: err.code, message: err.message, details: err.details } },
          { status: err.status, headers: { 'x-request-id': id } },
        )
      }

      log.error('request.unhandled', { durationMs, err })
      return NextResponse.json(
        { error: { code: 'internal_error', message: 'Something went wrong.', requestId: id } },
        { status: 500, headers: { 'x-request-id': id } },
      )
    })
}

/** Parses a JSON body, turning a malformed one into a 400 rather than a 500. */
export async function jsonBody(req: Request): Promise<unknown> {
  try {
    return await req.json()
  } catch {
    throw badRequest('malformed_json', 'Request body is not valid JSON.')
  }
}
