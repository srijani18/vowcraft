/**
 * The browser's client for the FastAPI backend.
 *
 * Lives here rather than in `packages/api-client` for now: with a single frontend the two
 * are equivalent, and promoting it to a workspace package means converting the repo to npm
 * workspaces — the disruptive step deliberately deferred until after cutover. Moving this
 * file later is a rename.
 *
 * Two jobs: know the backend's origin, and carry the bearer token.
 *
 * **Why a token rather than the cookie.** The backend is a different origin (Render vs
 * Vercel), and a `SameSite=Lax` cookie — which is what the Next.js session is — is simply
 * not sent cross-site. So the browser holds a JWT and attaches it explicitly.
 */

const TOKEN_KEY = 'v2b.accessToken'
const REFRESH_KEY = 'v2b.refreshToken'

export function apiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL
  if (!configured) {
    throw new Error(
      'NEXT_PUBLIC_API_URL is not set. The frontend does not know where the backend is.',
    )
  }
  return configured.replace(/\/$/, '')
}

export function apiConfigured(): boolean {
  return Boolean(process.env.NEXT_PUBLIC_API_URL)
}

/*
 * `sessionStorage`, not `localStorage`: the token dies with the tab, which bounds the
 * damage from a shared or forgotten machine. It is still readable by any script on the
 * page, so this is worse than an httpOnly cookie against XSS — an honest consequence of
 * the cross-origin split, and the reason the access token's lifetime is 30 minutes rather
 * than 14 days.
 */
export function storeTokens(tokens: { accessToken: string; refreshToken: string }): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.setItem(TOKEN_KEY, tokens.accessToken)
  window.sessionStorage.setItem(REFRESH_KEY, tokens.refreshToken)
}

export function accessToken(): string | null {
  if (typeof window === 'undefined') return null
  return window.sessionStorage.getItem(TOKEN_KEY)
}

export function clearTokens(): void {
  if (typeof window === 'undefined') return
  window.sessionStorage.removeItem(TOKEN_KEY)
  window.sessionStorage.removeItem(REFRESH_KEY)
}

/** Swaps the refresh token for a fresh pair. Returns false when the session is truly over. */
export async function refreshTokens(): Promise<boolean> {
  if (typeof window === 'undefined') return false
  const refreshToken = window.sessionStorage.getItem(REFRESH_KEY)
  if (!refreshToken) return false
  try {
    const response = await fetch(`${apiBaseUrl()}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ refreshToken }),
    })
    if (!response.ok) {
      clearTokens()
      return false
    }
    storeTokens((await response.json()) as { accessToken: string; refreshToken: string })
    return true
  } catch {
    return false
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

/**
 * A request to the backend, with the token attached and one transparent retry after a
 * refresh.
 *
 * The retry matters because a 30-minute access token *will* expire mid-session. Without it
 * the user is bounced to the login screen while holding a perfectly valid refresh token,
 * which reads as the app randomly signing them out.
 */
export async function apiFetch(path: string, init: RequestInit = {}, retrying = false): Promise<Response> {
  const token = accessToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('authorization', `Bearer ${token}`)
  // `FormData` sets its own multipart boundary; forcing JSON here would break every
  // file upload silently (the body is `[object FormData]` by the time the server sees it).
  if (init.body && !(init.body instanceof FormData) && !headers.has('content-type')) {
    headers.set('content-type', 'application/json')
  }

  const response = await fetch(`${apiBaseUrl()}${path}`, { ...init, headers })

  if (response.status === 401 && !retrying && (await refreshTokens())) {
    return apiFetch(path, init, true)
  }
  return response
}

/** JSON convenience that turns the documented error envelope into a thrown `ApiError`. */
export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, init)
  const body = (await response.json().catch(() => null)) as unknown
  if (!response.ok) {
    const error = (body as { error?: { code?: string; message?: string } } | null)?.error
    throw new ApiError(
      response.status,
      error?.code ?? 'request_failed',
      error?.message ?? 'The request failed.',
    )
  }
  return body as T
}

/**
 * The WebSocket URL for the live-transcription relay, plus the subprotocols that carry the
 * token.
 *
 * A browser cannot set an `Authorization` header when opening a WebSocket, so the token
 * travels as a subprotocol. Not a query parameter: query strings are logged verbatim by
 * proxies and load balancers, and a bearer token in a log is a bearer token in the wrong
 * hands.
 */
export function speechSocket(): { url: string; protocols: string[] } | null {
  const token = accessToken()
  if (!token) return null
  const base = apiBaseUrl().replace(/^http/, 'ws')
  return { url: `${base}/api/speech/stream`, protocols: ['bearer', token] }
}
