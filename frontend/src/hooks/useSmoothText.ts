import { useEffect, useRef, useState } from 'react'

import type { StreamBuffer } from '../lib/streamBuffer'

// How the streamed text is revealed.
//
// Tokens do not arrive evenly: the model sends a few words, pauses, then a burst (and after a
// tool call a whole sentence at once). Painting them as they come makes the text jump. Instead
// the text that has arrived is a TARGET, and once per animation frame the visible part moves
// towards it:
//
//   speed (chars/s) = clamp(backlog / DRAIN_S, MIN_CPS, MAX_CPS)   then at least backlog / MAX_LAG_S
//
// - a slow model: the text follows it closely (backlog / DRAIN_S), bursts spread over frames;
// - a fast model (gpt-4o-mini sends ~1000+ chars/s): the text is typed at a reading pace
//   (MAX_CPS) and the visible part falls behind the stream;
// - but never more than ~MAX_LAG_S behind: a very long answer speeds up instead of typing on
//   for ages after the model has finished. After the end the cap is DONE_MAX_CPS.
// With gpt-4o-mini: a 1800-char answer types in ~9.5 s, a 4200-char one in ~20 s (~190 chars/s).
// Slower / faster: lower / raise MAX_CPS and DONE_MAX_CPS (MAX_LAG_S bounds the long answers).
const DRAIN_S = 0.5 // how quickly a small backlog is caught up
const MIN_CPS = 50 // never slower than this (the last few characters before a pause)
const MAX_CPS = 130 // reading pace while the model is still writing (~2 chars per frame at 60 Hz)
const DONE_MAX_CPS = 220 // the stream has ended: finish a bit faster, still smoothly
const MAX_LAG_S = 12 // the visible text never lags more than ~12 s behind what has arrived
const MAX_DT_S = 0.1 // a frame after a long pause (a hidden tab) counts as at most 100 ms
const WORD_SNAP = 10 // finish the current word if it ends within this many characters

const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

/** where to stop: at the end of the current word if it is close, never inside a surrogate pair */
function snap(text: string, end: number): number {
  if (end >= text.length) return text.length
  const space = text.slice(end, end + WORD_SNAP).search(/\s/)
  if (space !== -1) end += space
  const code = text.charCodeAt(end - 1)
  if (code >= 0xd800 && code <= 0xdbff) end += 1 // the high half of an emoji
  return end
}

function commonPrefix(a: string, b: string): number {
  const n = Math.min(a.length, b.length)
  let i = 0
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++
  return i
}

/**
 * The part of a streaming answer that should be visible now. Re-renders at most once per
 * animation frame, and only while there is something new to show.
 * `settled` = the stream has ended and everything is visible.
 */
export function useSmoothText(buffer: StreamBuffer | undefined, finalText: string) {
  // a finished answer that is mounted again (back from the main screen) is shown whole at once,
  // not typed out a second time
  const [shown, setShown] = useState(() => (buffer?.done ? buffer.text : ''))
  const [settled, setSettled] = useState(() => !buffer || buffer.done)
  const shownRef = useRef(shown)

  useEffect(() => {
    if (!buffer) return
    const instant = reducedMotion()
    let raf = 0
    let last = 0
    let carry = 0 // the fraction of a character left over from the previous frame

    const tick = (now: number) => {
      raf = 0
      const target = buffer.text
      let visible = shownRef.current
      // the final text is not a continuation of what was shown (rare): continue from what is common
      if (!target.startsWith(visible)) visible = visible.slice(0, commonPrefix(visible, target))

      const backlog = target.length - visible.length
      if (backlog > 0) {
        let end = target.length
        if (!instant) {
          const dt = last ? Math.min(now - last, MAX_DT_S * 1000) / 1000 : 1 / 60
          const cap = buffer.done ? DONE_MAX_CPS : MAX_CPS
          const cps = Math.max(Math.min(Math.max(backlog / DRAIN_S, MIN_CPS), cap), backlog / MAX_LAG_S)
          carry += cps * dt
          const step = Math.max(0, Math.floor(carry))
          end = snap(target, visible.length + step)
          // finishing a word takes from the next frames' budget, so the average speed stays at cps
          carry -= end - visible.length
        }
        visible = target.slice(0, end)
      }
      if (visible !== shownRef.current) {
        shownRef.current = visible
        setShown(visible)
      }

      if (visible.length < target.length) {
        last = now
        raf = requestAnimationFrame(tick)
      } else {
        last = 0 // caught up: sleep until the next token (no idle frames)
        carry = 0
        if (buffer.done) setSettled(true)
      }
    }

    const wake = () => {
      if (!raf) raf = requestAnimationFrame(tick)
    }
    const unsubscribe = buffer.subscribe(wake)
    wake()
    return () => {
      unsubscribe()
      cancelAnimationFrame(raf)
    }
  }, [buffer])

  if (!buffer) return { text: finalText, settled: true }
  return { text: settled ? buffer.text : shown, settled }
}
