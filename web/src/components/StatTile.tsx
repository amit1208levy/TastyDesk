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
      className="lift sheened rounded-card border border-line bg-raised px-5 py-4 min-w-0"
      title={title}
    >
      <div className="truncate text-[12px] font-medium uppercase tracking-[0.13em] text-faint">
        {label}
      </div>
      <div
        key={flash}
        className={`num mt-1.5 truncate text-[28px] font-semibold leading-tight tabular-nums ${toneClass} ${
          flash ? 'rise' : ''
        }`}
      >
        {value}
      </div>
      {sub !== undefined && <div className="mt-1 truncate text-[13px] text-muted">{sub}</div>}
    </div>
  )
}
