import { useCallback, useEffect, useRef, useState } from 'react'

/* How wide the thing actually is on screen.

   An SVG that scales to fit its box scales its type with it, which is how a
   chart drawn for 520px ends up on a 1,500px screen with 32px axis labels and
   a line two and a half times too thick. Charts here are drawn at the width
   they are shown at instead, so a label stays the size it was chosen to be.

   It hands back a callback ref rather than taking one, because the elements
   being measured are usually behind a loading state: an effect that reads
   `ref.current` on mount finds nothing there, and never runs again once the
   real element arrives. A callback ref fires when the node attaches, whenever
   that is. */
export function useWidth<T extends HTMLElement>(): [(node: T | null) => void, number | null] {
  const [width, setWidth] = useState<number | null>(null)
  const observer = useRef<ResizeObserver | null>(null)

  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect()
    observer.current = null
    if (!node) return
    setWidth(node.clientWidth)
    // clientWidth, not the entry's contentRect: on a scrolling box the two
    // differ by the width of the scrollbar gutter, and a pane set to the
    // wider of the two ends up with its last ten pixels under the scrollbar.
    observer.current = new ResizeObserver(() => {
      if (node.clientWidth) setWidth(node.clientWidth)
    })
    observer.current.observe(node)
  }, [])

  return [ref, width]
}

/* Whether the screen is phone-narrow: under Tailwind's `sm` break, 640px.

   Most of the layout follows the width in CSS alone. This is for the few
   places where a phone needs a different arrangement rather than a smaller
   one, such as the positions table, whose columns become one card a row. */
const NARROW = '(max-width: 639px)'

export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW).matches)
  useEffect(() => {
    const query = window.matchMedia(NARROW)
    const update = () => setNarrow(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return narrow
}
