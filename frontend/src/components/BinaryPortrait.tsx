import { useEffect, useRef } from 'react'

import { createBinaryPortrait, type Crop } from '../lib/binaryPortrait'
import './BinaryPortrait.css'

interface Props {
  src: string
  crop?: Crop
  cellW?: number
  cellH?: number
}

export function BinaryPortrait({ src, crop, cellW, cellH }: Props) {
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    if (!wrap.current || !canvas.current) return
    const portrait = createBinaryPortrait(wrap.current, canvas.current, { src, crop, cellW, cellH })
    return portrait.destroy
    // crop is an object: compared by its numbers, so a new literal does not restart the animation
  }, [src, crop?.x, crop?.y, crop?.w, crop?.h, cellW, cellH])

  return (
    <div ref={wrap} className="binary-portrait">
      <canvas ref={canvas} role="img" aria-label="Portrait of Andriy made of animated zeros and ones" title="Click to decode again" />
    </div>
  )
}
