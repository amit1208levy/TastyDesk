import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/* The long explanation, on hold until it is wanted.

   The Positions table used to carry a strip of notes above it — four of the
   forty fields explained in a few words each, sitting between the heading and
   the numbers. It crowded the page, it was in the way, and it still left most
   columns unexplained.

   So the explanation moved here: hover a heading for two seconds and the whole
   paragraph appears. Two seconds is deliberate. A tooltip on a short delay
   fires while the eye is only passing over a row, which is what makes tooltips
   feel like flies; at two seconds it only ever appears because someone stopped
   and looked. The wait draws a thin brass line under the word so the pause
   reads as the app loading an answer rather than as nothing happening.

   The panel is a portal. Table headers live inside overflow-hidden and
   overflow-x-auto containers, which would clip anything positioned inside
   them, and it is pointer-transparent so it can never swallow the click that
   sorts the column underneath it. */

const DELAY_MS = 2000
const WIDTH = 340
const MARGIN = 12

type At = { x: number; y: number; above: boolean }

export function Help({
  title,
  body,
  footer,
  children,
  className,
  block,
}: {
  title: string
  body: string
  footer?: string
  children: ReactNode
  className?: string
  /** Inline by default; a block wrapper would break a table cell's layout. */
  block?: boolean
}) {
  const anchor = useRef<HTMLElement | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const [at, setAt] = useState<At | null>(null)

  const show = useCallback(() => {
    const el = anchor.current
    if (!el) return
    const r = el.getBoundingClientRect()
    // Below the word unless the bottom of the window is nearer than the top.
    const above = window.innerHeight - r.bottom < 190 && r.top > 190
    const half = WIDTH / 2
    const x = Math.min(Math.max(r.left + r.width / 2, half + MARGIN), window.innerWidth - half - MARGIN)
    setAt({ x, y: above ? r.top - 10 : r.bottom + 10, above })
  }, [])

  const hide = useCallback(() => {
    window.clearTimeout(timer.current)
    setAt(null)
  }, [])

  const wait = useCallback(() => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(show, DELAY_MS)
  }, [show])

  useEffect(() => () => window.clearTimeout(timer.current), [])

  useEffect(() => {
    if (!at) return
    const off = () => hide()
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') hide()
    }
    // Capture: the scroller is a div, not the window.
    window.addEventListener('scroll', off, true)
    window.addEventListener('resize', off)
    window.addEventListener('keydown', key)
    return () => {
      window.removeEventListener('scroll', off, true)
      window.removeEventListener('resize', off)
      window.removeEventListener('keydown', key)
    }
  }, [at, hide])

  const Tag = block ? 'div' : 'span'

  return (
    <>
      <Tag
        ref={anchor as never}
        onMouseEnter={wait}
        onMouseLeave={hide}
        // A keyboard user gets it at once: there is no pointer to pass over.
        onFocus={show}
        onBlur={hide}
        className={`help-anchor ${at ? 'is-open' : ''} ${block ? 'block' : 'inline-flex'} ${className ?? ''}`}
      >
        {children}
      </Tag>
      {at &&
        createPortal(
          <div
            role="tooltip"
            className="help-panel surface"
            style={{
              left: at.x,
              top: at.above ? undefined : at.y,
              bottom: at.above ? window.innerHeight - at.y : undefined,
            }}
          >
            <div className="label text-accent">{title}</div>
            <p className="mt-1.5 text-[14px] leading-relaxed text-ink">{body}</p>
            {footer && <p className="mt-2 text-[12px] text-faint">{footer}</p>}
          </div>,
          document.body,
        )}
    </>
  )
}
