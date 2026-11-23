import { memo, useMemo } from 'react'

import { renderMarkdown, splitBlocks, trimIncompleteTail } from '../lib/markdown'

// One top-level block. memo: a block whose Markdown did not change is not parsed, sanitised or
// written to the DOM again, so while an answer streams only the last block is touched per frame
// (and its pictures do not reload or blink).
const Block = memo(function Block({ md, highlight }: { md: string; highlight: boolean }) {
  const html = useMemo(() => renderMarkdown(md, { highlight }), [md, highlight])
  // the HTML is sanitised by DOMPurify in renderMarkdown()
  return <div className="md-block" dangerouslySetInnerHTML={{ __html: html }} />
})

export function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  const blocks = useMemo(() => splitBlocks(text), [text])
  return (
    <div className={streaming ? 'markdown-content streaming' : 'markdown-content'}>
      {blocks.map((md, i) => {
        const tail = streaming && i === blocks.length - 1
        // the block being typed: half-written Markdown hidden, highlighting left for the end
        return <Block key={i} md={tail ? trimIncompleteTail(md) : md} highlight={!tail} />
      })}
    </div>
  )
}
