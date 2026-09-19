import { useEffect, useRef, useState, type ReactNode } from 'react'

/* A headline figure.

   The label sits above the value in small caps, the value carries the weight,
   and a single line of context sits beneath. Nothing is coloured unless the
   number itself has a direction.

   Two pieces of motion, both earned. The tile lifts towards the cursor, which
   says it is a thing rather than a panel. And when the figure itself changes —
   a refresh brought a new price — it crossfades up instead of swapping, so a
   number that moved is a number you saw move. */
export function StatTile({
  label,
  value,
  sub,
  tone = 'neutral',
  title,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'neutral' | 'profit' | 'loss' | 'muted'
  title?: string
}) {
  const toneClass =
    tone === 'profit'
      ? 'text-profit'
      : tone === 'loss'
        ? 'text-loss'
        : tone === 'muted'
          ? 'text-muted'
          : 'text-ink'

  const printed = typeof value === 'string' || typeof value === 'number' ? String(value) : null
  // A figure is set large; a few words are not a figure. Sizing by length keeps
  // a phrase like "5 to decide" whole instead of clipping it to "5 to de…".
  const long = (printed?.length ?? 0) > 9
  const previous = useRef(printed)
  const [flash, setFlash] = useState(0)

  useEffect(() => {
    if (printed !== null && previous.current !== null && printed !== previous.current) {
      setFlash((n) => n + 1)
    }
    previous.current = printed
  }, [printed])

  return (
    <div
      className="lift tracked sheened surface min-w-0 px-6 py-5"
      title={title}
      onMouseMove={(e) => {
        // The light under the card follows the cursor across it.
        const box = e.currentTarget.getBoundingClientRect()
        e.currentTarget.style.setProperty('--mx', `${e.clientX - box.left}px`)
        e.currentTarget.style.setProperty('--my', `${e.clientY - box.top}px`)
      }}
    >
      <div className="label truncate">{label}</div>
      <div
        key={flash}
        className={`figure mt-2 truncate leading-none ${long ? 'text-[23px]' : 'text-[32px]'} ${
          toneClass
        } ${flash ? 'rise' : ''}`}
      >
        {value}
      </div>
      {sub !== undefined && <div className="mt-2 truncate text-[13px] text-muted">{sub}</div>}
    </div>
  )
}
