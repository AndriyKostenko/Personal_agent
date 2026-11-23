import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

const NEAR_BOTTOM_PX = 80

/**
 * Keeps a scroll container pinned to the bottom while its content grows, but only while the
 * visitor is already at the bottom: scrolling up to re-read something stops the following, and
 * scrolling back down (or the "jump to latest" button) resumes it.
 *
 * A ResizeObserver on the content replaces "scroll after every token": it fires once per layout
 * change, also when a picture finishes loading.
 */
export function useStickToBottom(scrollRef: RefObject<HTMLElement | null>, contentRef: RefObject<HTMLElement | null>) {
  const stuck = useRef(true)
  const [atBottom, setAtBottom] = useState(true)

  useEffect(() => {
    const scroller = scrollRef.current
    const content = contentRef.current
    if (!scroller || !content) return

    // Only a move UP by the visitor unsticks. "Far from the bottom" alone is not enough: the
    // content can grow by a whole fast frame between our scroll and its scroll event, which
    // would look like the visitor scrolled away.
    let lastTop = scroller.scrollTop
    let touchY = 0
    const update = (value: boolean) => {
      stuck.current = value
      setAtBottom(value)
    }
    const onScroll = () => {
      const top = scroller.scrollTop
      const near = scroller.scrollHeight - top - scroller.clientHeight < NEAR_BOTTOM_PX
      if (near) update(true)
      else if (top < lastTop) update(false) // scrollbar drag, keyboard, find-in-page...
      lastTop = top
    }
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) update(false) // react at once, before the scroll event
    }
    const onTouchStart = (e: TouchEvent) => {
      touchY = e.touches[0]?.clientY ?? 0
    }
    const onTouchMove = (e: TouchEvent) => {
      if ((e.touches[0]?.clientY ?? 0) > touchY) update(false) // finger moves down = content scrolls up
    }
    const observer = new ResizeObserver(() => {
      if (stuck.current) {
        scroller.scrollTop = scroller.scrollHeight
        lastTop = scroller.scrollTop
      }
    })
    scroller.addEventListener('scroll', onScroll, { passive: true })
    scroller.addEventListener('wheel', onWheel, { passive: true })
    scroller.addEventListener('touchstart', onTouchStart, { passive: true })
    scroller.addEventListener('touchmove', onTouchMove, { passive: true })
    observer.observe(content)
    return () => {
      scroller.removeEventListener('scroll', onScroll)
      scroller.removeEventListener('wheel', onWheel)
      scroller.removeEventListener('touchstart', onTouchStart)
      scroller.removeEventListener('touchmove', onTouchMove)
      observer.disconnect()
    }
  }, [scrollRef, contentRef])

  const scrollToBottom = useCallback(
    (smooth = false) => {
      const scroller = scrollRef.current
      if (!scroller) return
      stuck.current = true
      setAtBottom(true)
      scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
    },
    [scrollRef],
  )

  const scrollToTop = useCallback(() => {
    stuck.current = false
    setAtBottom(false)
    scrollRef.current?.scrollTo({ top: 0 })
  }, [scrollRef])

  return { atBottom, scrollToBottom, scrollToTop }
}
