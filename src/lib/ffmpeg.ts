import { spawn } from 'node:child_process'

/**
 * Whether FFmpeg is on PATH — surfaced by `/api/health`'s `videoUploadSupported` so an
 * operator learns it from a health check rather than from a failed upload.
 *
 * The ingest pipeline that used to call this to actually demux video now lives in the
 * FastAPI backend (SPEC-015 §7, `apps/api/app/services/ingest/audio.py`); this is the one
 * piece the Next.js health check still needs directly.
 */

const PROBE_TIMEOUT_MS = 30_000

let cachedAvailability: boolean | null = null

export async function ffmpegAvailable(): Promise<boolean> {
  if (cachedAvailability !== null) return cachedAvailability
  cachedAvailability = await new Promise<boolean>((resolve) => {
    const child = spawn('ffmpeg', ['-version'], { stdio: 'ignore' })
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      resolve(false)
    }, PROBE_TIMEOUT_MS)
    child.on('error', () => {
      clearTimeout(timer)
      resolve(false)
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve(code === 0)
    })
  })
  return cachedAvailability
}
