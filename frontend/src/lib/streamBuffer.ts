/**
 * The text of an answer that is still arriving.
 *
 * Tokens are appended HERE, not to React state: a state update per token would re-render the
 * chat 30-100 times a second, and a burst of tokens would be painted in one jump anyway. The
 * component that shows the answer subscribes and reveals the text at its own, smooth pace
 * (hooks/useSmoothText.ts), at most once per animation frame.
 */
export class StreamBuffer {
  text = ''
  /** no more text will arrive (the answer is complete, or the stream was cut off) */
  done = false
  private listeners = new Set<() => void>()

  append(chunk: string) {
    this.text += chunk
    this.emit()
  }

  /** the complete answer: replaces what was streamed (normally the same text) */
  finish(finalText?: string) {
    if (finalText !== undefined) this.text = finalText
    this.done = true
    this.emit()
  }

  subscribe(listener: () => void) {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private emit() {
    for (const listener of this.listeners) listener()
  }
}
