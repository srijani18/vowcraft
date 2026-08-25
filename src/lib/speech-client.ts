/**
 * Transcript accumulation for the live surface — SPEC-014 §3.
 *
 * Everything vendor-specific that used to live here — auth modes, frame-shape dot-paths,
 * container negotiation — moved into the FastAPI relay, which now sends one normalised
 * shape. What remains is the interim/final bookkeeping, which is ours, is pure, and is the
 * part most likely to be got wrong.
 */

/** One utterance, or the in-progress guess at one. */
export interface SpeechFrame {
  text: string
  final: boolean
  /**
   * The provider's diarization index for whoever spoke this, or null/undefined when the
   * provider does not diarize. Kept as the raw index rather than a "Speaker 1" string:
   * turning it into a label is a presentation decision made once, server-side, where
   * segments are built.
   */
  speaker?: number | null
}

/**
 * Accumulates frames into a transcript.
 *
 * Kept as a class with no DOM or socket knowledge so it is unit-testable: the interim/final
 * distinction is the part most likely to be got wrong, and it is pure bookkeeping.
 *
 * Interim frames *replace* the tail; final frames commit it. A naive implementation that
 * appended every frame would repeat each phrase several times as the provider refined its
 * guess — the most visible possible bug on this screen.
 */
export class TranscriptAccumulator {
  private committed: string[] = []
  private interim = ''
  private _utterances: { text: string; atMs: number; speaker?: number | null }[] = []

  /**
   * `atMs` is optional and additive — the voice-to-BRD flow (SPEC-014) never passes it and
   * only ever reads `finalText`/`displayText`; live meeting capture (SPEC-013) passes it to
   * build `utterances`, since a `Transcript`'s segments need an approximate timestamp per
   * utterance and the live relay's frames carry none of their own (SPEC-014 §3's
   * `Utterance` has no timing at all — only `text`/`isFinal`).
   */
  add(frame: SpeechFrame, atMs?: number): void {
    if (frame.final) {
      const text = frame.text.trim()
      this.committed.push(text)
      if (atMs !== undefined && text) this._utterances.push({ text, atMs, speaker: frame.speaker ?? null })
      this.interim = ''
    } else {
      this.interim = frame.text.trim()
    }
  }

  /** Everything finalised so far — what gets sent for generation. */
  get finalText(): string {
    return this.committed.join(' ').replace(/\s+/g, ' ').trim()
  }

  /** Finalised plus the current guess — what gets rendered while speaking. */
  get displayText(): string {
    return [this.finalText, this.interim].filter(Boolean).join(' ').trim()
  }

  /** Each finalised utterance with the timestamp it arrived at — only populated when
   * `add()` was called with `atMs`. Interim frames never appear here. */
  get utterances(): { text: string; atMs: number; speaker?: number | null }[] {
    return this._utterances
  }

  get isEmpty(): boolean {
    return this.committed.length === 0 && !this.interim
  }

  reset(): void {
    this.committed = []
    this.interim = ''
    this._utterances = []
  }
}

/** What MediaRecorder should produce. Opus in WebM where available; the backend copes.
 * Shared by SPEC-014's mic dictation and SPEC-013's tab capture — identical requirement,
 * one implementation. */
export function pickMimeType(): string | null {
  if (typeof MediaRecorder === 'undefined') return null
  for (const candidate of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate
  }
  return null
}
