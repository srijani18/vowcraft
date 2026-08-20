import { currentUser } from './auth'
import { mintApiAccessToken } from './api-token'

/**
 * The Next.js **server's** client for the FastAPI backend — SPEC-015 §7.
 *
 * Distinct from `api-client.ts`, which is the *browser's* client. A React Server
 * Component runs during SSR with the session cookie (via `next/headers`), not the
 * browser's `sessionStorage` where the long-lived bearer token pair lives — so it
 * cannot reuse that client. Instead, this module mints a token (see `api-token.ts`)
 * for whichever user the session cookie resolved to and calls the backend directly,
 * server to server.
 *
 * This is only safe because `web` and `api` share `JWT_SECRET` (see the comment in
 * `docker-compose.yml`): the token minted here is a real token FastAPI's
 * `decode_token` will accept, not a proxy or an impersonation channel. It is
 * deliberately short-lived (60s) — long enough for one SSR render, not a credential
 * worth stealing from a log.
 */

function internalApiBaseUrl(): string {
  const configured = process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL
  if (!configured) {
    throw new Error('Neither INTERNAL_API_URL nor NEXT_PUBLIC_API_URL is set.')
  }
  return configured.replace(/\/$/, '')
}

/**
 * A request to the backend made from the Next.js server on behalf of whoever the
 * session cookie identifies. Throws `UnauthenticatedError` (from `./auth`) the same
 * way `currentUser()` does when there is no session — callers already handle that.
 */
export async function apiServerFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const user = await currentUser()
  const token = mintApiAccessToken(user)
  const headers = new Headers(init.headers)
  headers.set('authorization', `Bearer ${token}`)
  if (init.body && !headers.has('content-type')) headers.set('content-type', 'application/json')
  return fetch(`${internalApiBaseUrl()}${path}`, { ...init, headers, cache: 'no-store' })
}

export class ApiServerError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

export async function apiServerJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiServerFetch(path, init)
  const body = (await response.json().catch(() => null)) as unknown
  if (!response.ok) {
    const error = (body as { error?: { code?: string; message?: string } } | null)?.error
    throw new ApiServerError(
      response.status,
      error?.code ?? 'request_failed',
      error?.message ?? 'The request failed.',
    )
  }
  return body as T
}
