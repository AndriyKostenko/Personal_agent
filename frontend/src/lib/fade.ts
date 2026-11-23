/**
 * The soft "fade in" of newly typed words (the ChatGPT / Claude look).
 *
 * The block that is being typed is re-rendered from Markdown on every frame, so its DOM is
 * thrown away and rebuilt each time. A plain CSS animation on the new words would restart on
 * every frame. Instead:
 *   1. we remember WHEN each range of characters of the block first appeared (`births`);
 *   2. after every render the words younger than FADE_MS are wrapped in <span class="md-fade">;
 *   3. each span gets a NEGATIVE animation-delay = its age, so a re-created span continues its
 *      fade from where the previous one was instead of starting again.
 * Older words stay plain text nodes: at any moment only the last dozen or so words are spans.
 */

export const FADE_MS = 500 // keep in sync with the md-fade animation in styles/app.css

interface Birth {
  start: number // offset in the block's textContent
  t: number // performance.now() when the characters from `start` appeared
}

export interface FadeState {
  length: number // textContent length at the last render
  births: Birth[] // ascending by start (and by time)
}

export const newFadeState = (): FadeState => ({ length: 0, births: [] })

/**
 * Call right after the block's HTML was written (before paint).
 * `record`: the text is still being revealed, so growth is new text. Without it (a stored
 * answer, or a block that was just re-rendered with highlighting) nothing new is born and
 * only the words that are still fading are wrapped again.
 */
export function applyFade(root: HTMLElement, state: FadeState, record: boolean) {
  const now = performance.now()
  const length = root.textContent?.length ?? 0

  if (length < state.length) {
    state.births = state.births.filter((b) => b.start < length) // the text was hidden or replaced
  } else if (length > state.length && record) {
    state.births.push({ start: state.length, t: now })
  }
  state.length = length

  // forget what has finished fading, but keep the ranges that are still fading
  while (state.births.length && now - state.births[0]!.t >= FADE_MS) state.births.shift()
  const firstYoung = state.births[0]?.start
  if (firstYoung === undefined) return

  const birthAt = (offset: number) => {
    let t = state.births[0]!.t
    for (const b of state.births) {
      if (b.start > offset) break
      t = b.t
    }
    return t
  }

  // collect first, then mutate (a TreeWalker breaks when its nodes are replaced)
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes: { node: Text; offset: number }[] = []
  let offset = 0
  for (let n = walker.nextNode() as Text | null; n; n = walker.nextNode() as Text | null) {
    const len = n.data.length
    // skip old text and text that is already wrapped (a second effect run on the same DOM)
    if (offset + len > firstYoung && !n.parentElement?.classList.contains('md-fade')) {
      nodes.push({ node: n, offset })
    }
    offset += len
  }

  for (const { node, offset: base } of nodes) {
    const fragment = document.createDocumentFragment()
    let plain = ''
    for (const match of node.data.matchAll(/\S+|\s+/g)) {
      const at = base + (match.index ?? 0)
      let word = match[0]
      if (at + word.length <= firstYoung || !/\S/.test(word)) {
        plain += word // old, or whitespace (never wrapped: an invisible span is wasted work)
        continue
      }
      if (at < firstYoung) {
        // the start of the word is already fully visible: only its new end fades in
        plain += word.slice(0, firstYoung - at)
        word = word.slice(firstYoung - at)
      }
      if (plain) {
        fragment.append(plain)
        plain = ''
      }
      const span = document.createElement('span')
      span.className = 'md-fade'
      span.style.animationDelay = `${-Math.round(now - birthAt(Math.max(at, firstYoung)))}ms`
      span.textContent = word
      fragment.append(span)
    }
    if (plain) fragment.append(plain)
    node.replaceWith(fragment)
  }
}
