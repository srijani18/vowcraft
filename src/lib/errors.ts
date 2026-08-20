/**
 * The error vocabulary: a domain error carrying an HTTP shape, plus constructors for
 * the statuses this codebase actually uses.
 *
 * Separate from `lib/http.ts` because that module imports `next/server` for the
 * response mapping, and anything importing it becomes unusable outside a Next
 * runtime — including in `node --test`. The *type* of an error is not a framework
 * concern, so it lives here and `http.ts` re-exports it for callers that want both.
 *
 * (The same split exists for `session.ts` / `session-cookie.ts` and
 * `password-rules.ts` / `password.ts`, for the same reason each time.)
 */

/**
 * Domain error with an HTTP shape. `code` is stable and client-branchable.
 *
 * Fields are declared and assigned explicitly rather than using TypeScript's
 * parameter-property shorthand: that shorthand needs a real transform, so a module
 * containing it cannot be imported by `node --test`, which only strips types. This
 * class sits under most of the codebase, so making it strip-compatible is what keeps
 * the whole unit suite able to import from `lib/`.
 */
export class AppError extends Error {
  readonly status: number
  readonly code: string
  readonly details?: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.name = 'AppError'
    this.status = status
    this.code = code
    this.details = details
  }
}

export const badRequest = (code: string, m: string, d?: unknown) => new AppError(400, code, m, d)
export const notFound = (m = 'Not found') => new AppError(404, 'not_found', m)
export const conflict = (code: string, m: string, d?: unknown) => new AppError(409, code, m, d)
export const unprocessable = (code: string, m: string, d?: unknown) => new AppError(422, code, m, d)
export const badGateway = (code: string, m: string, d?: unknown) => new AppError(502, code, m, d)
