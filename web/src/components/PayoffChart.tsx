import { useMemo, useState } from 'react'
import { money, num, EM_DASH } from '../lib/format'

export interface PayoffCurve {
  points: { price: string; pnl: string }[]
  breakevens: string[]
  strikes: string[]
  spot: string | null
  max_profit: string | null
  max_loss: string | null
}

const W = 520
const H = 170
const PAD = { top: 10, right: 12, bottom: 22, left: 46 }

/* Profit and loss at expiration across the underlying's range.

   The domain is anchored on the strikes rather than on the current price, so
   the structure is always in frame — a strangle whose underlying has run past
   the short call still shows both wings and where the trade turns over.

   Zero is a real boundary here, not a convenience, so the fill diverges around
   it. Colour is not carrying that alone: the line's side of the baseline says
   the same thing, and the axis is labelled. */
export function PayoffChart({ curve }: { curve: PayoffCurve }) {
  const [hover, setHover] = useState<number | null>(null)

  const model = useMemo(() => {
    const pts = curve.points
      .map((p) => ({ price: num(p.price), pnl: num(p.pnl) }))
      .filter((p): p is { price: number; pnl: number } => p.price !== null && p.pnl !== null)
    if (pts.length < 2) return null

    const xMin = pts[0].price
    const xMax = pts[pts.length - 1].price
    const yVals = pts.map((p) => p.pnl)
    let yMin = Math.min(...yVals, 0)
    let yMax = Math.max(...yVals, 0)
    const span = yMax - yMin || 1
    yMin -= span * 0.08
    yMax += span * 0.08

    const x = (v: number) => PAD.left + ((v - xMin) / (xMax - xMin)) * (W - PAD.left - PAD.right)
    const y = (v: number) => PAD.top + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom)

    return { pts, x, y, xMin, xMax, yMin, yMax, zeroY: y(0) }
  }, [curve])

  if (!model) return <div className="text-[14px] text-faint">{EM_DASH}</div>

  const { pts, x, y, xMin, xMax, zeroY } = model
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.price).toFixed(1)},${y(p.pnl).toFixed(1)}`).join(' ')
  const areaTop = `${line} L${x(pts[pts.length - 1].price).toFixed(1)},${zeroY.toFixed(1)} L${x(pts[0].price).toFixed(1)},${zeroY.toFixed(1)} Z`

  const hovered = hover === null ? null : pts[Math.max(0, Math.min(pts.length - 1, hover))]

  return (
    <div className="w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Profit and loss at expiration across the underlying price"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect()
          const px = ((e.clientX - rect.left) / rect.width) * W
          const t = (px - PAD.left) / (W - PAD.left - PAD.right)
          setHover(Math.round(t * (pts.length - 1)))
        }}
      >
        <defs>
          <clipPath id="above-zero">
            <rect x={0} y={0} width={W} height={zeroY} />
          </clipPath>
          <clipPath id="below-zero">
            <rect x={0} y={zeroY} width={W} height={H - zeroY} />
          </clipPath>
        </defs>

        <path d={areaTop} className="fill-chart-profit/25" clipPath="url(#above-zero)" />
        <path d={areaTop} className="fill-chart-loss/18" clipPath="url(#below-zero)" />

        {curve.strikes.map((s) => {
          const v = num(s)
          if (v === null || v < xMin || v > xMax) return null
          return (
            <line
              key={`k${s}`}
              x1={x(v)}
              x2={x(v)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              className="stroke-line"
              strokeWidth={1}
              strokeDasharray="2 3"
            />
          )
        })}

        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={zeroY}
          y2={zeroY}
          className="stroke-line-strong"
          strokeWidth={1}
        />

        {curve.breakevens.map((b) => {
          const v = num(b)
          if (v === null || v < xMin || v > xMax) return null
          return <circle key={`b${b}`} cx={x(v)} cy={zeroY} r={3} className="fill-ink" />
        })}

        <path d={line} fill="none" className="stroke-ink" strokeWidth={2} strokeLinejoin="round" />

        {curve.spot !== null &&
          (() => {
            const v = num(curve.spot)
            if (v === null || v < xMin || v > xMax) return null
            return (
              <g>
                <line
                  x1={x(v)}
                  x2={x(v)}
                  y1={PAD.top}
                  y2={H - PAD.bottom}
                  className="stroke-accent"
                  strokeWidth={1.5}
                />
                <text x={x(v)} y={PAD.top + 8} textAnchor="middle" className="fill-accent text-[11px]">
                  now
                </text>
              </g>
            )
          })()}

        {hovered && (
          <g>
            <line
              x1={x(hovered.price)}
              x2={x(hovered.price)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              className="stroke-line-strong"
              strokeWidth={1}
            />
            <circle
              cx={x(hovered.price)}
              cy={y(hovered.pnl)}
              r={4}
              className={hovered.pnl >= 0 ? 'fill-chart-profit' : 'fill-chart-loss'}
              stroke="var(--bg-raised)"
              strokeWidth={2}
            />
          </g>
        )}

        <text x={PAD.left} y={H - 6} className="fill-faint text-[11px]">
          {xMin.toFixed(0)}
        </text>
        <text x={W - PAD.right} y={H - 6} textAnchor="end" className="fill-faint text-[11px]">
          {xMax.toFixed(0)}
        </text>
        <text x={PAD.left - 4} y={zeroY + 3} textAnchor="end" className="fill-faint text-[11px]">
          $0
        </text>
        <text x={PAD.left - 4} y={PAD.top + 7} textAnchor="end" className="fill-faint text-[11px]">
          {money(model.yMax, { cents: false })}
        </text>
        <text x={PAD.left - 4} y={H - PAD.bottom - 1} textAnchor="end" className="fill-faint text-[11px]">
          {money(model.yMin, { cents: false })}
        </text>
      </svg>

      <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[13px] text-faint">
        <span>
          Breakeven{curve.breakevens.length === 1 ? '' : 's'}{' '}
          <span className="num text-muted">
            {curve.breakevens.length ? curve.breakevens.map((b) => num(b)?.toFixed(2)).join(' / ') : EM_DASH}
          </span>
        </span>
        {hovered && (
          <span className="ml-auto">
            At <span className="num text-muted">{hovered.price.toFixed(2)}</span> this expires{' '}
            <span className={`num ${hovered.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
              {money(hovered.pnl, { sign: true, cents: false })}
            </span>
          </span>
        )}
      </div>
    </div>
  )
}
