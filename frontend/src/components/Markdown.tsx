import { memo, useLayoutEffect, useMemo, useRef } from 'react'

import { applyFade, newFadeState, type FadeState } from '../lib/fade'
import { renderMarkdown, splitBlocks, trimIncompleteTail } from '../lib/markdown'

interface BlockProps {
  md: string
  highlight: boolean
  /** the fade-in bookkeeping of this block; only for answers that are typed out live */
  fade?: FadeState
  /** the text is still being revealed: growth of the block is new text that fades in */
  record: boolean
}

// One top-level block. memo: a block whose Markdown did not change is not parsed, sanitised or
// written to the DOM again, so while an answer streams only the last block is touched per frame
// (and its pictures do not reload or blink).
const Block = memo(function Block({ md, highlight, fade, record }: BlockProps) {
  const ref = useRef<HTMLDivElement>(null)
  const html = useMemo(() => renderMarkdown(md, { highlight }), [md, highlight])
  const recordRef = useRef(record)
  recordRef.current = record

  // after React wrote new HTML and before the browser paints it: wrap the young words
  useLayoutEffect(() => {
    if (fade && ref.current) applyFade(ref.current, fade, recordRef.current)
  }, [html, fade])

  // the HTML is sanitised by DOMPurify in renderMarkdown()
  return <div ref={ref} className="md-block" dangerouslySetInnerHTML={{ __html: html }} />
})

interface Props {
  text: string
  /** the text is still being revealed (no highlighting in the last block) */
  streaming?: boolean
  /** new words fade in (only for an answer that is typed out live, never for a stored one) */
  animate?: boolean
}

export function Markdown({ text, streaming = false, animate = false }: Props) {
  const blocks = useMemo(() => splitBlocks(text), [text])
  const fades = useRef<FadeState[]>([])
  return (
    <div className={streaming ? 'markdown-content streaming' : 'markdown-content'}>
      {blocks.map((md, i) => {
        const tail = streaming && i === blocks.length - 1
        const fade = animate ? (fades.current[i] ??= newFadeState()) : undefined
        // the block being typed: half-written Markdown hidden, highlighting left for the end
        return <Block key={i} md={tail ? trimIncompleteTail(md) : md} highlight={!tail} fade={fade} record={streaming} />
      })}
    </div>
  )
}
