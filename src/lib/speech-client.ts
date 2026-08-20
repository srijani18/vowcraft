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

  add(frame: SpeechFrame): void {
    if (frame.final) {
      this.committed.push(frame.text.trim())
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

  get isEmpty(): boolean {
    return this.committed.length === 0 && !this.interim
  }

  reset(): void {
    this.committed = []
    this.interim = ''
  }
}
