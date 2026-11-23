import type { ChatEvent } from '../types'

/**
 * Reads Server-Sent Events ("data: {...}\n\n") from a fetch response.
 *
 * fetch + a streamed body instead of EventSource: EventSource can only GET and can not send a
 * JSON body. Network chunks do not line up with event boundaries, so the trailing, incomplete
 * frame is kept in the buffer until the next read.
 */
export async function* readEvents(body: ReadableStream<BufferSource>): AsyncGenerator<ChatEvent> {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() ?? '' // the last part may be incomplete
      for (const frame of frames) {
        const event = parseFrame(frame)
        if (event) yield event
      }
    }
    const last = parseFrame(buffer) // a stream that ended without the final blank line
    if (last) yield last
  } finally {
    reader.releaseLock()
  }
}

// a frame may have several "data:" lines (joined with \n) and comment lines (": ping")
function parseFrame(frame: string): ChatEvent | null {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(line.startsWith('data: ') ? 6 : 5))
    .join('\n')
  if (!data) return null
  try {
    return JSON.parse(data) as ChatEvent
  } catch {
    console.warn('bad SSE frame', frame)
    return null
  }
}
