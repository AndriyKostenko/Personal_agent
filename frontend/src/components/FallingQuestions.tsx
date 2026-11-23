import { useState, type CSSProperties } from 'react'

import './FallingQuestions.css'

// Question tabs that drift down along both sides of the screen. Position, speed and sway are
// random; when a tab reaches the bottom it comes back from the top with another question.
// A click sends the question; hovering a tab freezes it so it is easy to hit.

type Side = 'left' | 'right'

interface Slot {
  key: number
  side: Side
  text: string
  x: number // 0 = against the screen edge, 1 = against the conversation
  dur: number
  delay: number
  sway: number
  swayDur: number
  tilt: number
}

const PER_SIDE = 4
const SIDES: Side[] = ['left', 'right']
const rand = (min: number, max: number) => min + Math.random() * (max - min)
let seq = 0

function pickText(questions: string[], slots: Slot[]): string {
  const used = new Set(slots.map((s) => s.text))
  const free = questions.filter((q) => !used.has(q))
  const pool = free.length ? free : questions
  return pool[Math.floor(Math.random() * pool.length)] ?? ''
}

// `index` spreads the first tabs over the whole height (a negative delay = already on its way)
function makeSlot(side: Side, text: string, index = -1): Slot {
  const dur = rand(18, 32)
  return {
    key: ++seq,
    side,
    text,
    x: rand(0, 1),
    dur,
    delay: index < 0 ? 0 : -(dur * (index + rand(0, 0.8))) / PER_SIDE,
    sway: rand(8, 24),
    swayDur: rand(4, 8),
    tilt: rand(-1.6, 1.6),
  }
}

function initialSlots(questions: string[]): Slot[] {
  const slots: Slot[] = []
  for (const side of SIDES) {
    for (let i = 0; i < PER_SIDE; i++) slots.push(makeSlot(side, pickText(questions, slots), i))
  }
  return slots
}

interface Props {
  questions: string[]
  disabled?: boolean
  onPick: (question: string) => void
}

export function FallingQuestions({ questions, disabled = false, onPick }: Props) {
  const [slots, setSlots] = useState(() => initialSlots(questions))

  // a new key = a new element: the animation restarts from the top
  const respawn = (key: number) =>
    setSlots((list) => list.map((s) => (s.key === key ? makeSlot(s.side, pickText(questions, list)) : s)))

  const pick = (slot: Slot) => {
    if (disabled) return
    onPick(slot.text)
    respawn(slot.key)
  }

  return SIDES.map((side) => (
    <div key={side} className={`rail ${side}`} aria-label="Suggested questions">
      {slots
        .filter((s) => s.side === side)
        .map((slot) => (
          <div
            key={slot.key}
            className="fall"
            style={{ '--x': slot.x, '--dur': `${slot.dur}s`, '--delay': `${slot.delay}s` } as CSSProperties}
            onAnimationIteration={(e) => {
              if (e.target === e.currentTarget) respawn(slot.key)
            }}
          >
            <div
              className="sway"
              style={{ '--sway': `${slot.sway}px`, '--sway-dur': `${slot.swayDur}s`, '--tilt': `${slot.tilt}deg` } as CSSProperties}
            >
              <button type="button" className="tab frame" disabled={disabled} onClick={() => pick(slot)}>
                <span className="tab-tag">{'// ASK'}</span>
                <span className="tab-text">{slot.text}</span>
                <span className="tab-arrow">↗</span>
              </button>
            </div>
          </div>
        ))}
    </div>
  ))
}
