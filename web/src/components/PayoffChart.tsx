import { useMemo, useState } from 'react'
import { money, num, EM_DASH } from '../lib/format'
import { useWidth } from '../lib/useMeasure'

export interface PayoffCurve {
  points: { price: string; pnl: string }[]
  breakevens: string[]
  strikes: string[]
  spot: string | null
  max_profit: string | null
  max_loss: string | null
  /** False when the legs do not all expire together, so the line is a shape. */
  exact: boolean
  note: string | null
}

const H = 240
const PAD = { top: 30, right: 20, bottom: 36, left: 70 }
/* Two labels closer than this are one smudge. */
const CLEAR = 15

/* Profit and loss at expiration across the underlying's range.

   The domain is anchored on the strikes rather than on the current price, so
   the structure is always in frame — a strangle whose underlying has run past
   the short call still shows both wings and where the trade turns over.

   Zero is a real boundary here, not a convenience, so the fill diverges around
   it. Colour is not carrying that alone: the line's side of the baseline says
   the same thing, and the axis is labelled.

   It is drawn at the width it is shown at. Scaling one drawing to fit any box
   scales the type with it, which put 32px axis labels and a 6px line on a wide
   screen and stacked the top label on top of the zero label whenever the most
   a position could make was small beside what it could lose. */
export function PayoffChart({ curve }: { curve: PayoffCurve }) {
  const [box, W] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)

  const model = useMemo(() => {
    if (!W) return null
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
  }, [curve, W])

  const hovered = model && hover !== null ? model.pts[Math.max(0, Math.min(model.pts.length - 1, hover))] : null

  return (
    <div ref={box} className="w-full">
      {!model || !W ? (
        <div style={{ height: H }} />
      ) : (
        (() => {
          const { pts, x, y, xMin, xMax, zeroY } = model
          const line = pts
            .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.price).toFixed(1)},${y(p.pnl).toFixed(1)}`)
            .join(' ')
          const area = `${line} L${x(pts[pts.length - 1].price).toFixed(1)},${zeroY.toFixed(1)} L${x(
            pts[0].price,
          ).toFixed(1)},${zeroY.toFixed(1)} Z`

          const top = PAD.top
          const floor = H - PAD.bottom
          const spot = num(curve.spot)
          const strikes = curve.strikes.map(num).filter((v): v is number => v !== null && v >= xMin && v <= xMax)

          // The bottom axis: the strikes, which are the prices that matter,
          // plus the ends of the range where they do not crowd one.
          const ticks: { v: number; strong: boolean }[] = strikes.map((v) => ({ v, strong: true }))
          for (const end of [xMin, xMax]) {
            if (!ticks.some((t) => Math.abs(x(t.v) - x(end)) < 34)) ticks.push({ v: end, strong: false })
          }

          return (
            <svg
              width={W}
              height={H}
              viewBox={`0 0 ${W} ${H}`}
              // Until the observer catches a narrower box, scale rather than
              // push everything beside it off the screen.
              className="max-w-full"
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

              <path d={area} className="fill-chart-profit/25" clipPath="url(#above-zero)" />
              <path d={area} className="fill-chart-loss/18" clipPath="url(#below-zero)" />

              {strikes.map((v) => (
                <line
                  key={`k${v}`}
                  x1={x(v)}
                  x2={x(v)}
                  y1={top}
                  y2={floor}
                  className="stroke-line-strong"
                  strokeWidth={1}
                  strokeDasharray="2 4"
                />
              ))}

              <line x1={PAD.left} x2={W - PAD.right} y1={zeroY} y2={zeroY} className="stroke-line-strong" strokeWidth={1} />
              <line x1={PAD.left} x2={W - PAD.right} y1={floor} y2={floor} className="stroke-line" strokeWidth={1} />

              <path d={line} fill="none" className="stroke-ink" strokeWidth={2} strokeLinejoin="round" />

              {curve.breakevens.map((b) => {
                const v = num(b)
                if (v === null || v < xMin || v > xMax) return null
                return (
                  <circle
                    key={`b${b}`}
                    cx={x(v)}
                    cy={zeroY}
                    r={3.5}
                    className="fill-bg stroke-ink"
                    strokeWidth={2}
                  />
                )
              })}

              {/* Where the underlying is, in the band above the plot rather
                  than written across the line it is pointing at. */}
              {spot !== null && spot >= xMin && spot <= xMax && (
                <g>
                  <line x1={x(spot)} x2={x(spot)} y1={top - 6} y2={floor} className="stroke-accent" strokeWidth={1.5} />
                  <text
                    x={Math.min(Math.max(x(spot), PAD.left + 26), W - PAD.right - 26)}
                    y={top - 11}
                    textAnchor="middle"
                    className="fill-accent text-[11px] font-medium uppercase tracking-wider"
                  >
                    now {spot.toFixed(2)}
                  </text>
                </g>
              )}

              {hovered && (
                <g>
                  <line
                    x1={x(hovered.price)}
                    x2={x(hovered.price)}
                    y1={top}
                    y2={floor}
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

              {ticks.map((t) => (
                <text
                  key={`t${t.v}`}
                  x={x(t.v)}
                  y={floor + 15}
                  textAnchor="middle"
                  className={`text-[11px] ${t.strong ? 'fill-muted' : 'fill-faint'}`}
                >
                  {t.v.toFixed(t.strong ? 2 : 0).replace(/\.00$/, '')}
                </text>
              ))}

              {/* Money down the left. The top and bottom of the range are
                  dropped when zero has already taken that corner — the most a
                  covered call can make is often a hair above nothing. */}
              <text x={PAD.left - 8} y={zeroY + 4} textAnchor="end" className="fill-muted text-[11px]">
                $0
              </text>
              {Math.abs(zeroY - top) > CLEAR && (
                <text x={PAD.left - 8} y={top + 4} textAnchor="end" className="fill-faint text-[11px]">
                  {money(model.yMax, { cents: false, sign: true })}
                </text>
              )}
              {Math.abs(zeroY - floor) > CLEAR && (
                <text x={PAD.left - 8} y={floor} textAnchor="end" className="fill-faint text-[11px]">
                  {money(model.yMin, { cents: false, sign: true })}
                </text>
              )}
            </svg>
          )
        })()
      )}

      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-[13px] text-faint">
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

      {curve.note && (
        <p className="mt-2 border-t border-line pt-2 text-[13px] leading-relaxed text-tested">{curve.note}</p>
      )}
    </div>
  )
}
