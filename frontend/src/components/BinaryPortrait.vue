<script setup>
// A photo drawn with animated "0" and "1" (the idea of the "image of 1s and 0s" tutorial):
//   1. the photo is shrunk to a grid, one pixel per character cell;
//   2. the brightness of the pixel decides how bright (and which colour) the digit is;
//   3. every frame the digits flip randomly, so the portrait "shimmers".
// On load a scan line "decodes" the portrait out of falling binary rain; click replays it,
// the pointer makes the digits around it glitch.
import { ref, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  src: { type: String, required: true },
  // the part of the photo that is drawn (fractions of the image size)
  crop: { type: Object, default: () => ({ x: 0.16, y: 0.02, w: 0.62, h: 0.44 }) },
  cellW: { type: Number, default: 4.4 }, // CSS px of one digit cell
  cellH: { type: Number, default: 7 },
})

const wrap = ref(null)
const canvas = ref(null)

const LEVELS = 24 // brightness steps (each step has its own pre-rendered glyph)
const INTRO = 2.6 // seconds of the "decoding" animation
const TAIL = 14 // length of a rain streak, in rows
const CUT = 0.2 // darker cells are left empty
const AMBIENT = 0.12 // brightness of the rain around the portrait after the intro
const FRAME_MS = 33 // ~30 fps is plenty for this effect

// palette: #643D36 -> #9F1F0D -> #E5571C -> #E5953A -> #CECECE
const STOPS = [
  [0, [100, 61, 54]],
  [0.3, [159, 31, 13]],
  [0.62, [229, 87, 28]],
  [0.84, [229, 149, 58]],
  [1, [206, 206, 206]],
]

function ramp(t) {
  for (let k = 1; k < STOPS.length; k++) {
    const [t1, c1] = STOPS[k]
    if (t <= t1) {
      const [t0, c0] = STOPS[k - 1]
      const f = (t - t0) / (t1 - t0)
      return c0.map((v, i) => Math.round(v + (c1[i] - v) * f))
    }
  }
  return STOPS[STOPS.length - 1][1]
}

let img = null
let ctx = null
let atlas = null
let sw = 0
let sh = 0
let cols = 0
let rows = 0
let target = null // brightness 0..1 of the portrait for every cell
let glyph = null // current digit (0/1) of every cell
let colSpeed = null
let colPhase = null
let rafId = 0
let startTime = 0
let lastDraw = 0
let lastWidth = 0
let reduced = false
let observer = null
const mouse = { x: 0, y: 0, active: false }

const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v)

// one sprite per (digit, brightness): drawing a frame is then just drawImage calls
function buildAtlas() {
  atlas = document.createElement('canvas')
  atlas.width = sw * LEVELS * 2
  atlas.height = sh
  const g = atlas.getContext('2d')
  g.font = `700 ${Math.round(sh * 0.92)}px ui-monospace, Menlo, Consolas, monospace`
  g.textAlign = 'center'
  g.textBaseline = 'middle'
  for (let digit = 0; digit < 2; digit++) {
    for (let lvl = 1; lvl < LEVELS; lvl++) {
      const t = lvl / (LEVELS - 1)
      const [r, gr, b] = ramp(t)
      g.fillStyle = `rgba(${r},${gr},${b},${0.4 + 0.6 * t})`
      g.fillText(String(digit), (digit * LEVELS + lvl) * sw + sw / 2, sh / 2 + 1)
    }
  }
}

function lumaOf(d, i) {
  return (0.2126 * d[i * 4] + 0.7152 * d[i * 4 + 1] + 0.0722 * d[i * 4 + 2]) / 255
}

// photo -> brightness grid. The wall behind is as bright as the skin, so plain brightness
// gives a flat picture: the figure is cut out by its difference from the wall colour, and
// the local contrast (pixel minus its blurred neighbourhood) brings out eyes, nose, mouth.
function buildTarget() {
  const small = document.createElement('canvas')
  small.width = cols
  small.height = rows
  const sg = small.getContext('2d', { willReadFrequently: true })
  sg.imageSmoothingQuality = 'high'
  const { x, y, w, h } = props.crop
  const iw = img.naturalWidth
  const ih = img.naturalHeight
  sg.drawImage(img, x * iw, y * ih, w * iw, h * ih, 0, 0, cols, rows)
  const d = sg.getImageData(0, 0, cols, rows).data
  const n = cols * rows

  // blurred copy: shrink and stretch back
  const tiny = document.createElement('canvas')
  tiny.width = Math.ceil(cols / 5)
  tiny.height = Math.ceil(rows / 5)
  const tg = tiny.getContext('2d')
  tg.imageSmoothingQuality = 'high'
  tg.drawImage(small, 0, 0, tiny.width, tiny.height)
  const blurred = document.createElement('canvas')
  blurred.width = cols
  blurred.height = rows
  const bg = blurred.getContext('2d', { willReadFrequently: true })
  bg.imageSmoothingQuality = 'high'
  bg.drawImage(tiny, 0, 0, cols, rows)
  const b = bg.getImageData(0, 0, cols, rows).data

  // wall colour = average of the top corners
  let wr = 0
  let wg = 0
  let wb = 0
  let wn = 0
  const cw = Math.max(1, Math.floor(cols * 0.15))
  for (let j = 0; j < Math.max(1, Math.floor(rows * 0.2)); j++) {
    for (let i = 0; i < cw * 2; i++) {
      const xx = i < cw ? i : cols - cw + (i - cw)
      const k = (j * cols + xx) * 4
      wr += d[k]
      wg += d[k + 1]
      wb += d[k + 2]
      wn++
    }
  }
  wr /= wn
  wg /= wn
  wb /= wn

  const raw = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const dist = Math.hypot(d[i * 4] - wr, d[i * 4 + 1] - wg, d[i * 4 + 2] - wb) / 255
    const figure = Math.min(1, dist * 2.6)
    const l = lumaOf(d, i)
    const feature = clamp01(0.5 + (l - lumaOf(b, i)) * 4.2 + (l - 0.45) * 0.9)
    raw[i] = figure * feature
  }

  // stretch the 3rd..97th percentile to 0..1
  const sorted = Float32Array.from(raw).sort()
  const lo = sorted[Math.floor(n * 0.03)]
  const hi = sorted[Math.floor(n * 0.97)]
  const span = hi - lo || 1

  target = new Float32Array(n)
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const nx = (i / cols - 0.5) * 2
      const ny = (j / rows - 0.5) * 2
      const vignette = clamp01((1.12 - Math.hypot(nx * 0.9, ny * 0.85)) * 5)
      const v = clamp01((raw[j * cols + i] - lo) / span) * vignette
      target[j * cols + i] = v < CUT ? 0 : v
    }
  }
}

function initCells() {
  const n = cols * rows
  glyph = new Uint8Array(n)
  for (let i = 0; i < n; i++) glyph[i] = Math.random() < 0.5 ? 0 : 1
  colSpeed = new Float32Array(cols)
  colPhase = new Float32Array(cols)
  for (let i = 0; i < cols; i++) {
    colSpeed[i] = 10 + Math.random() * 18
    colPhase[i] = Math.random() * (rows + TAIL)
  }
}

function build() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  sw = Math.max(2, Math.round(props.cellW * dpr))
  sh = Math.max(3, Math.round(props.cellH * dpr))
  const cssWidth = wrap.value.clientWidth
  lastWidth = cssWidth
  cols = Math.max(24, Math.floor((cssWidth * dpr) / sw))
  const aspect = (props.crop.h * img.naturalHeight) / (props.crop.w * img.naturalWidth)
  rows = Math.max(24, Math.round((cols * sw * aspect) / sh))

  const cv = canvas.value
  cv.width = cols * sw
  cv.height = rows * sh
  cv.style.width = `${cv.width / dpr}px`
  cv.style.height = `${cv.height / dpr}px`
  ctx = cv.getContext('2d')

  buildAtlas()
  buildTarget()
  initCells()
}

function draw(t) {
  ctx.clearRect(0, 0, cols * sw, rows * sh)

  // the scan line that decodes the portrait: moves from the top to the bottom during INTRO
  const front = reduced ? rows + 10 : (t / INTRO) * (rows + 6) - 3
  // a bright band that sweeps over the finished portrait every few seconds
  const sweep = t > INTRO ? (((t - INTRO) / 5) % 1.5) * (rows + 8) - 4 : -99
  const ambient = reduced ? 0 : AMBIENT
  const radius = 7
  const cycle = rows + TAIL

  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const idx = j * cols + i
      const tv = target[idx]

      // binary rain: a streak falls down every column
      const dRain = ((colPhase[i] + t * colSpeed[i]) % cycle) - j
      let rain = 0
      if (dRain >= 0 && dRain < TAIL) rain = dRain < 1 ? 1 : Math.pow(1 - dRain / TAIL, 1.6) * 0.8

      let v
      let decoded = true
      if (j > front + 1.5) {
        v = rain * 0.9 // not decoded yet: only rain
        decoded = false
      } else if (j > front - 1.5) {
        v = 1 // the scan line itself
      } else if (tv > 0) {
        v = tv * (0.82 + 0.18 * Math.sin(t * 2.2 + idx * 0.37))
        if (Math.abs(j - sweep) < 3) v = Math.min(1, v + 0.4 * (1 - Math.abs(j - sweep) / 3))
      } else {
        v = rain * ambient // faint rain around the portrait
      }

      // the pointer excites the cells near it
      let hot = false
      if (mouse.active) {
        const dx = i - mouse.x
        const dy = j - mouse.y
        const dist = Math.sqrt(dx * dx + dy * dy)
        if (dist < radius) {
          hot = true
          v = Math.max(v, 0.95 * (1 - dist / radius))
        }
      }

      if (!reduced && Math.random() < (hot ? 0.35 : decoded ? 0.012 : 0.15)) glyph[idx] ^= 1

      const lvl = Math.round(v * (LEVELS - 1))
      if (lvl <= 0) continue
      ctx.drawImage(atlas, (glyph[idx] * LEVELS + lvl) * sw, 0, sw, sh, i * sw, j * sh, sw, sh)
    }
  }
}

function frame(now) {
  rafId = requestAnimationFrame(frame)
  if (now - lastDraw < FRAME_MS) return
  lastDraw = now
  draw((now - startTime) / 1000)
}

function onMove(e) {
  const rect = canvas.value.getBoundingClientRect()
  mouse.x = ((e.clientX - rect.left) / rect.width) * cols
  mouse.y = ((e.clientY - rect.top) / rect.height) * rows
  mouse.active = true
}

const onLeave = () => {
  mouse.active = false
}

// click: decode the portrait again
function replay() {
  startTime = performance.now()
  if (reduced) draw(0)
}

onMounted(async () => {
  reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  img = new Image()
  img.src = props.src
  try {
    await img.decode()
  } catch {
    return // no photo: the component just stays empty
  }
  if (!wrap.value) return // unmounted while the photo was loading

  build()
  startTime = performance.now()
  if (reduced) draw(0)
  else rafId = requestAnimationFrame(frame)

  // rebuild the grid when the container changes its width
  observer = new ResizeObserver(() => {
    if (!wrap.value || Math.abs(wrap.value.clientWidth - lastWidth) < 8) return
    build()
    if (reduced) draw(0)
  })
  observer.observe(wrap.value)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(rafId)
  observer?.disconnect()
})

defineExpose({ replay })
</script>

<template>
  <div ref="wrap" class="binary-portrait">
    <canvas
      ref="canvas"
      role="img"
      aria-label="Portrait of Andriy made of animated zeros and ones"
      title="Click to decode again"
      @pointermove="onMove"
      @pointerleave="onLeave"
      @click="replay"
    ></canvas>
  </div>
</template>

<style scoped>
.binary-portrait {
  width: 100%;
}

canvas {
  display: block;
  max-width: 100%;
  margin: 0 auto;
  cursor: crosshair;
  filter: drop-shadow(0 0 14px rgba(229, 87, 28, 0.3));
}
</style>
