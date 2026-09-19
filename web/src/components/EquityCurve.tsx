import { useMemo, useState } from 'react'
import { money, shortDate } from '../lib/format'
import type { CurvePoint } from '../lib/stats'

const W = 640
const H = 120
const PAD = { top: 8, right: 10, bottom: 16, left: 52 }

/* Cumulative realized P&L, in the order the trades closed.

   Hand-drawn rather than charted by a library because it is redrawn on every
   pixel of the confidence slider, and the whole point of that control is that
   the line moves while you drag it. Zero is a real boundary and is drawn as
   one; the line's side of it, not only its colour, says which way the strategy
   went. */
export function EquityCurve({ points }: { points: CurvePoint[] }) {
  const [hover, setHover] = useState<number | null>(null)

  const model = useMemo(() => {
    if (points.length === 0) return null
    const values = points.map((p) => p.cumulative)
    const hi = Math.max(0, ...values)
    const lo = Math.min(0, ...values)
    const span = hi - lo || 1
    const innerW = W - PAD.left - PAD.right
    const innerH = H - PAD.top - PAD.bottom

    const x = (i: number) =>
      PAD.left + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW)
    const y = (v: number) => PAD.top + innerH - ((v - lo) / span) * innerH

    const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(p.cumulative)}`).join(' ')
    const zeroY = y(0)
    const area = `${line} L${x(points.length - 1)},${zeroY} L${x(0)},${zeroY} Z`

    return { x, y, line, area, zeroY, hi, lo, innerW }
  }, [points])

  if (!model) {
    return (
      <div className="flex h-[120px] items-center justify-center text-[13px] text-faint">
        No closed trades at this confidence.
      </div>
    )
  }

  const last = points[points.length - 1]
  const up = last.cumulative >= 0
  const shown = hover === null ? null : points[hover]

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`Cumulative profit and loss across ${points.length} closed trades, ending at ${money(last.cumulative, { cents: false })}`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect()
          const rel = ((e.clientX - box.left) / box.width) * W
          const step = model.innerW / Math.max(points.length - 1, 1)
          const i = Math.round((rel - PAD.left) / step)
          setHover(Math.max(0, Math.min(points.length - 1, i)))
        }}
      >
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={model.zeroY}
          y2={model.zeroY}
          className="stroke-line-strong"
          strokeDasharray="3 3"
        />
        <path
          d={model.area}
          className={`fade-in ${up ? 'fill-profit/10' : 'fill-loss/10'}`}
        />
        <path
          key={points.length}
          d={model.line}
          fill="none"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          /* The line draws itself left to right, the way it was earned. Keyed
             on the point count so it redraws when the threshold changes what
             is in the strategy. */
          className={`draw-in ${up ? 'stroke-profit' : 'stroke-loss'}`}
          style={{ ['--draw-length' as string]: String(Math.max(points.length, 2) * 60) }}
        />

        <text x={4} y={PAD.top + 8} className="fill-faint text-[11px]">
          {money(model.hi, { cents: false })}
        </text>
        <text x={4} y={H - PAD.bottom} className="fill-faint text-[11px]">
          {money(model.lo, { cents: false })}
        </text>
        <text x={PAD.left} y={H - 3} className="fill-faint text-[11px]">
          {shortDate(points[0].date)}
        </text>
        <text x={W - PAD.right} y={H - 3} textAnchor="end" className="fill-faint text-[11px]">
          {shortDate(last.date)}
        </text>

        {shown && (
          <>
            <line
              x1={model.x(hover!)}
              x2={model.x(hover!)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              className="stroke-line-strong"
            />
            <circle
              cx={model.x(hover!)}
              cy={model.y(shown.cumulative)}
              r={3}
              className={up ? 'fill-profit' : 'fill-loss'}
            />
          </>
        )}
      </svg>

      <div className="mt-0.5 h-4 text-[12px] text-muted">
        {shown ? (
          <span className="num">
            {shortDate(shown.date)} · trade {money(shown.pnl, { sign: true, cents: false })} ·
            running {money(shown.cumulative, { sign: true, cents: false })}
          </span>
        ) : (
          <span className="text-faint">
            {points.length} closed trade{points.length === 1 ? '' : 's'}, oldest first
          </span>
        )}
      </div>
    </div>
  )
}
