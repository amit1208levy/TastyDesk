import { useMemo, useState } from 'react'
import { money, shortDate } from '../lib/format'
import type { CurvePoint } from '../lib/stats'

const W = 640
const H = 120
const PAD = { top: 8, right: 10, bottom: 16, left: 52 }

/* Cumulative P&L: what was banked, then what is still running.

   The realized part is in close order and drawn solid. The open part carries
   on from the last close in the order the trades were opened, drawn in brass
   and broken, because it is a different kind of number -- a mark that can
   still move rather than a result. Mixing the two in one solid line would
   claim a strategy had made money it has not yet taken.

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

    const at = (i: number) => `${x(i)},${y(points[i].cumulative)}`
    const zeroY = y(0)

    // Where the banked part ends and the running part begins. The open stretch
    // starts at the last closed point, so the two lines meet rather than
    // leaving a gap the eye has to jump.
    const firstOpen = points.findIndex((p) => p.open)
    const lastClosed = firstOpen === -1 ? points.length - 1 : Math.max(firstOpen - 1, 0)

    const seg = (from: number, to: number) =>
      to < from
        ? ''
        : points
            .slice(from, to + 1)
            .map((_, k) => `${k === 0 ? 'M' : 'L'}${at(from + k)}`)
            .join(' ')

    // The drawn length of the realized line, in user units. It used to be
    // guessed from the number of points, and any curve longer than the guess
    // kept its tail permanently hidden behind the dash that reveals it —
    // which, with the open stretch drawn past that tail, left the two looking
    // disconnected. Measured here instead, from the same coordinates the path
    // is built from.
    let drawLength = 0
    for (let i = 1; i <= lastClosed; i += 1) {
      const dx = x(i) - x(i - 1)
      const dy = y(points[i].cumulative) - y(points[i - 1].cumulative)
      drawLength += Math.sqrt(dx * dx + dy * dy)
    }

    const realized = firstOpen === 0 ? '' : seg(0, lastClosed)
    const open = firstOpen === -1 ? '' : seg(lastClosed, points.length - 1)
    const area = realized
      ? `${realized} L${x(lastClosed)},${zeroY} L${x(0)},${zeroY} Z`
      : ''

    return { x, y, realized, open, area, zeroY, hi, lo, innerW, drawLength }
  }, [points])

  if (!model) {
    return (
      <div className="flex h-[120px] items-center justify-center text-[13px] text-faint">
        Nothing to plot at this confidence.
      </div>
    )
  }

  const last = points[points.length - 1]
  const up = last.cumulative >= 0
  const shown = hover === null ? null : points[hover]
  const openCount = points.filter((p) => p.open).length
  const closedCount = points.filter((p) => !p.open && !p.start).length

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`Cumulative profit and loss across ${closedCount} closed trades${
          openCount ? ` and ${openCount} still open` : ''
        }, ending at ${money(last.cumulative, { cents: false })}`}
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
        {model.area && (
          <path d={model.area} className={`fade-in ${up ? 'fill-profit/10' : 'fill-loss/10'}`} />
        )}
        {model.realized && (
          <path
            key={`r${points.length}`}
            d={model.realized}
            fill="none"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            /* The line draws itself left to right, the way it was earned. Keyed
               on the point count so it redraws when the threshold changes what
               is in the strategy. */
            className={`draw-in ${up ? 'stroke-profit' : 'stroke-loss'}`}
            style={{ ['--draw-length' as string]: String(Math.ceil(model.drawLength) + 2) }}
          />
        )}
        {/* What is still open, in brass and broken: it is a mark that can move,
            not a result that is banked, and it should not look like one. */}
        {model.open && (
          <path
            d={model.open}
            fill="none"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray="5 4"
            className="stroke-accent"
          />
        )}

        <text x={4} y={PAD.top + 8} className="fill-faint text-[11px]">
          {money(model.hi, { cents: false })}
        </text>
        <text x={4} y={H - PAD.bottom} className="fill-faint text-[11px]">
          {money(model.lo, { cents: false })}
        </text>
        <text x={PAD.left} y={H - 3} className="fill-faint text-[11px]">
          {shortDate(points[0].date)}
        </text>
        {/* The right edge is "now" when the line ends on something still
            open: that point is today's mark, not the day the trade began. */}
        <text
          x={W - PAD.right}
          y={H - 3}
          textAnchor="end"
          className={`text-[11px] ${last.open ? 'fill-accent' : 'fill-faint'}`}
        >
          {last.open ? 'now' : shortDate(last.date)}
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
              className={shown.open ? 'fill-accent' : up ? 'fill-profit' : 'fill-loss'}
            />
          </>
        )}
      </svg>

      <div className="mt-0.5 h-4 text-[12px] text-muted">
        {shown?.start ? (
          <span className="num">nothing closed yet — the line starts at $0</span>
        ) : shown ? (
          <span className="num">
            {shown.open ? `opened ${shortDate(shown.date)}` : shortDate(shown.date)} ·{' '}
            {shown.open ? <span className="text-accent">still open</span> : 'trade'}{' '}
            {money(shown.pnl, { sign: true, cents: false })} · running{' '}
            {money(shown.cumulative, { sign: true, cents: false })}
          </span>
        ) : (
          <span className="text-faint">
            {closedCount} closed trade{closedCount === 1 ? '' : 's'}, oldest first
            {openCount > 0 && (
              <span className="text-accent">
                {' '}
                · dashed: {openCount} still open, not yet realised
              </span>
            )}
          </span>
        )}
      </div>
    </div>
  )
}
