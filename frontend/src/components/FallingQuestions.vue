<script setup>
import { ref } from 'vue'

// Question tabs that drift down along both sides of the screen. Position, speed and sway are
// random; when a tab reaches the bottom it comes back from the top with another question.
// A click sends the question; hovering a tab freezes it so it is easy to hit.
const props = defineProps({
  questions: { type: Array, required: true },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['pick'])

const PER_SIDE = 4
const rand = (min, max) => min + Math.random() * (max - min)

let seq = 0
const slots = ref([])

const pickText = () => {
  const used = new Set(slots.value.map((s) => s.text))
  const free = props.questions.filter((q) => !used.has(q))
  const pool = free.length ? free : props.questions
  return pool[Math.floor(Math.random() * pool.length)]
}

// `index` spreads the first tabs over the whole height (a negative delay = already on its way)
const makeSlot = (side, index = -1) => {
  const dur = rand(18, 32)
  return {
    key: ++seq,
    side,
    text: pickText(),
    x: rand(0, 1), // 0 = against the screen edge, 1 = against the conversation
    dur,
    delay: index < 0 ? 0 : -(dur * (index + rand(0, 0.8))) / PER_SIDE,
    sway: rand(8, 24),
    swayDur: rand(4, 8),
    tilt: rand(-1.6, 1.6),
  }
}

for (const side of ['left', 'right']) {
  for (let i = 0; i < PER_SIDE; i++) slots.value.push(makeSlot(side, i))
}

// a new key = a new element: the animation restarts from the top
const respawn = (slot) => {
  const i = slots.value.findIndex((s) => s.key === slot.key)
  if (i !== -1) slots.value[i] = makeSlot(slot.side)
}

const pick = (slot) => {
  if (props.disabled) return
  emit('pick', slot.text)
  respawn(slot)
}

const bySide = (side) => slots.value.filter((s) => s.side === side)
</script>

<template>
  <div v-for="side in ['left', 'right']" :key="side" class="rail" :class="side" aria-label="Suggested questions">
    <div
      v-for="slot in bySide(side)"
      :key="slot.key"
      class="fall"
      :style="{
        '--x': slot.x,
        '--dur': `${slot.dur}s`,
        '--delay': `${slot.delay}s`,
      }"
      @animationiteration.self="respawn(slot)"
    >
      <div
        class="sway"
        :style="{ '--sway': `${slot.sway}px`, '--sway-dur': `${slot.swayDur}s`, '--tilt': `${slot.tilt}deg` }"
      >
        <button type="button" class="tab frame" :disabled="disabled" @click="pick(slot)">
          <span class="tab-tag">// ASK</span>
          <span class="tab-text">{{ slot.text }}</span>
          <span class="tab-arrow">↗</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* only where there is room beside the conversation column (780px wide) */
.rail {
  --w: min(210px, 100%);
  position: fixed;
  top: 48px; /* below the status bar */
  bottom: 0;
  z-index: 5;
  width: calc((100vw - 780px) / 2 - 60px);
  overflow: hidden;
  pointer-events: none;
}

.rail.left {
  left: 30px;
}

.rail.right {
  right: 30px;
}

.fall {
  position: absolute;
  top: 0;
  left: calc((100% - var(--w)) * var(--x));
  width: var(--w);
  animation: fall var(--dur) linear var(--delay) infinite;
  will-change: transform;
}

.sway {
  animation: sway var(--sway-dur) ease-in-out infinite alternate;
}

@keyframes fall {
  from {
    transform: translateY(-110px);
    opacity: 0;
  }
  8% {
    opacity: 1;
  }
  90% {
    opacity: 1;
  }
  to {
    transform: translateY(100vh);
    opacity: 0;
  }
}

@keyframes sway {
  from {
    transform: translateX(calc(var(--sway) * -1)) rotate(calc(var(--tilt) * -1));
  }
  to {
    transform: translateX(var(--sway)) rotate(var(--tilt));
  }
}

.fall:hover,
.fall:hover .sway {
  animation-play-state: paused;
}

.tab {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 100%;
  padding: 11px 30px 12px 12px;
  text-align: left;
  color: var(--ink);
  background: rgba(13, 13, 13, 0.86);
  border: 1px solid var(--line-strong);
  cursor: pointer;
  pointer-events: auto;
  backdrop-filter: blur(6px);
  transition: border-color 0.2s, background-color 0.2s;
}

.tab:hover:not(:disabled) {
  background: rgba(229, 87, 28, 0.1);
  border-color: var(--orange);
}

.tab:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.tab-tag {
  font-family: var(--mono);
  font-size: 0.54rem;
  letter-spacing: 0.18em;
  color: var(--orange);
}

.tab-text {
  font-family: var(--sans);
  font-size: 0.78rem;
  line-height: 1.4;
  color: var(--ink-strong);
}

.tab-arrow {
  position: absolute;
  top: 9px;
  right: 11px;
  font-family: var(--mono);
  font-size: 0.8rem;
  color: var(--orange);
}

@media (max-width: 1199px), (prefers-reduced-motion: reduce) {
  .rail {
    display: none;
  }
}
</style>
