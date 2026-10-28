import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { useWidth } from '../lib/useMeasure'
import { fullDate, money, num, pct, shortDate } from '../lib/format'
import type { StrategyReport } from '../types'

/* A strategy beside the thing it was traded on.

   Two panes over one time axis, the way a trading chart stacks an indicator
   under price: the product's daily close on top, the strategy's running P&L
   under it. Not one pane with two scales — dollars of P&L and points of price
   have nothing in common, and a second axis invites reading a crossing of the
   two lines as meaning something. One cursor runs through both, and the caption
   under them reads both at that day.

   "Compare in $" is the one exception, because there both lines are dollars:
   what holding the product from the first day would have made (one futures
   contract, or 100 shares) beside what trading it made. Same unit, one scale. */

const TOP_H = 150
const BOT_H = 120
const GAP = 14
const PAD = { top: 8, right: 10, bottom: 18, left: 60 }

const DAY = 86_400_000

function dayOf(iso: string) {
  return Date.parse(`${iso.slice(0, 10)}T12:00:00Z`)
}

export function AgainstUnderlying({ report }: { report: StrategyReport }) {
  const product = report.product
  const start = report.first
  const history = useAsync(
    () => (product && start ? api.underlyingHistory(product, start) : Promise.resolve(null)),
    [product, start],
  )
  const [box, measured] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  // Compare: one dollar chart, holding the product beside trading it.
  const [compare, setCompare] = useState(true)
  const W = measured ?? 640
  const pointValue = num(history.data?.point_value ?? null)

  const model = useMemo(() => {
    const bars = (history.data?.bars ?? []).map((b) => ({ t: dayOf(b.date), date: b.date, close: num(b.close) ?? 0 }))
    if (bars.length < 2) return null
    // Running P&L on each trading day: everything closed on or before it.
    const closes = report.curve
      .map((p) => ({ t: dayOf(p.date), cumulative: num(p.cumulative) ?? 0 }))
      .sort((a, b) => a.t - b.t)
    let k = 0
    let running = 0
    const days = bars.map((b) => {
      while (k < closes.length && closes[k].t <= b.t + DAY / 2) running = closes[k++].cumulative
      return { ...b, pnl: running }
    })
    // Trades closed after the last bar (today, before the close prints).
    if (k < closes.length) days[days.length - 1].pnl = closes[closes.length - 1].cumulative

    const t0 = days[0].t
    const t1 = days[days.length - 1].t
    const innerW = W - PAD.left - PAD.right
    const x = (t: number) => PAD.left + ((t - t0) / (t1 - t0 || 1)) * innerW

    const prices = days.map((d) => d.close)
    const pHi = Math.max(...prices)
    const pLo = Math.min(...prices)
    const pSpan = pHi - pLo || 1
    const pInner = TOP_H - PAD.top - 6
    const yP = (v: number) => PAD.top + pInner - ((v - pLo) / pSpan) * pInner

    const pnls = days.map((d) => d.pnl)
    const qHi = Math.max(0, ...pnls)
    const qLo = Math.min(0, ...pnls)
    const qSpan = qHi - qLo || 1
    const top2 = TOP_H + GAP
    const qInner = BOT_H - 6 - PAD.bottom
    const yQ = (v: number) => top2 + 6 + qInner - ((v - qLo) / qSpan) * qInner

    const priceLine = days.map((d, i) => `${i ? 'L' : 'M'}${x(d.t).toFixed(1)},${yP(d.close).toFixed(1)}`).join(' ')
    // A step: P&L only moves on the day a trade closes.
    const pnlLine = days
      .map((d, i) =>
        i === 0
          ? `M${x(d.t).toFixed(1)},${yQ(d.pnl).toFixed(1)}`
          : `H${x(d.t).toFixed(1)} V${yQ(d.pnl).toFixed(1)}`,
      )
      .join(' ')

    // Compare: what holding it from the first day would have made, in dollars,
    // against the strategy's running P&L, on one dollar scale.
    const held = pointValue === null ? null : days.map((d) => (d.close - days[0].close) * pointValue)
    const cAll = [0, ...pnls, ...(held ?? [])]
    const cHi = Math.max(...cAll)
    const cLo = Math.min(...cAll)
    const cSpan = cHi - cLo || 1
    const cInner = TOP_H + GAP + BOT_H - PAD.top - PAD.bottom - 6
    const yC = (v: number) => PAD.top + cInner - ((v - cLo) / cSpan) * cInner
    const heldLine = held
      ? days.map((d, i) => `${i ? 'L' : 'M'}${x(d.t).toFixed(1)},${yC(held[i]).toFixed(1)}`).join(' ')
      : null
    const tradedLine = days
      .map((d, i) =>
        i === 0 ? `M${x(d.t).toFixed(1)},${yC(d.pnl).toFixed(1)}` : `H${x(d.t).toFixed(1)} V${yC(d.pnl).toFixed(1)}`,
      )
      .join(' ')

    return {
      days, x, yP, yQ, priceLine, pnlLine, pHi, pLo, qHi, qLo, top2, innerW,
      held, yC, cHi, cLo, heldLine, tradedLine,
    }
  }, [history.data, report.curve, W, pointValue])

  if (!product || !start) return null

  const first = model?.days[0]
  const last = model?.days[model.days.length - 1]
  const move = first && last ? last.close / first.close - 1 : null
  const shown = model && hover !== null ? model.days[hover] : null
  const H = TOP_H + GAP + BOT_H
  const up = (last?.pnl ?? 0) >= 0
  const name = history.data?.product ?? product
  const canCompare = !!model?.held
  const comparing = compare && canCompare
  const heldNow = model?.held ? model.held[model.held.length - 1] : null
  const heldAt = (i: number) => (model?.held ? model.held[i] : null)

  return (
    <div className="surface sheened px-6 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="flex items-center gap-3">
          <div className="label">Against {name}</div>
          {canCompare && (
            <div className="flex overflow-hidden rounded-full border border-line text-[12px]">
              {([true, false] as const).map((v) => (
                <button
                  key={String(v)}
                  onClick={() => setCompare(v)}
                  className={`px-2.5 py-0.5 ${compare === v ? 'bg-sunken font-medium text-ink' : 'text-muted hover:text-ink'}`}
                >
                  {v ? 'Compare in $' : 'Price & P&L'}
                </button>
              ))}
            </div>
          )}
        </div>
        {comparing && heldNow !== null && last ? (
          <span className="num text-[13px] text-muted">
            Holding {history.data?.holding}{' '}
            <span className={heldNow >= 0 ? 'text-profit' : 'text-loss'}>{money(heldNow, { sign: true, cents: false })}</span>
            {' · '}your trading{' '}
            <span className={up ? 'text-profit' : 'text-loss'}>{money(last.pnl, { sign: true, cents: false })}</span>
          </span>
        ) : model && first && last && move !== null && (
          <span className="num text-[13px] text-muted">
            {name} {pct(move, 1, true)} since {fullDate(first.date)} · this strategy{' '}
            <span className={up ? 'text-profit' : 'text-loss'}>{money(last.pnl, { sign: true, cents: false })}</span>
          </span>
        )}
      </div>
      <div ref={box} className="mt-3">
        {history.error ? (
          <p className="py-6 text-[14px] text-muted">No price history for {name} right now.</p>
        ) : !history.data ? (
          <div style={{ height: H }} />
        ) : !model ? (
          <p className="py-6 text-[14px] text-muted">
            No price history for {name}
            {history.data.error ? ` (${history.data.error})` : ''}.
          </p>
        ) : (
          <svg
            viewBox={`0 0 ${W} ${H}`}
            width={W}
            height={H}
            className="block max-w-full"
            role="img"
            aria-label={`${name} moved ${pct(move, 1, true)} while this strategy made ${money(last!.pnl, { sign: true, cents: false })}`}
            onMouseLeave={() => setHover(null)}
            onMouseMove={(e) => {
              const r = e.currentTarget.getBoundingClientRect()
              const rel = ((e.clientX - r.left) / r.width) * W
              const t = model.days[0].t + ((rel - PAD.left) / model.innerW) * (model.days[model.days.length - 1].t - model.days[0].t)
              let best = 0
              for (let i = 1; i < model.days.length; i += 1)
                if (Math.abs(model.days[i].t - t) < Math.abs(model.days[best].t - t)) best = i
              setHover(best)
            }}
          >
            {comparing && model.heldLine && (
              <>
                {[model.cHi, model.cLo].filter((v, i, a) => v !== 0 && a.indexOf(v) === i).map((v) => (
                  <g key={`c${v}`}>
                    <line x1={PAD.left} x2={W - PAD.right} y1={model.yC(v)} y2={model.yC(v)} className="stroke-chart-grid" strokeWidth={1} />
                    <text x={PAD.left - 8} y={model.yC(v) + 4} textAnchor="end" className="fill-faint text-[11px]">
                      {money(v, { sign: true, cents: false })}
                    </text>
                  </g>
                ))}
                <line x1={PAD.left} x2={W - PAD.right} y1={model.yC(0)} y2={model.yC(0)} className="stroke-line-strong" strokeDasharray="3 3" />
                <text x={PAD.left - 8} y={model.yC(0) + 4} textAnchor="end" className="fill-faint text-[11px]">
                  $0
                </text>
                <path d={model.heldLine} fill="none" strokeWidth={2} strokeLinejoin="round" className="stroke-accent" />
                <path d={model.tradedLine} fill="none" strokeWidth={2.5} strokeLinejoin="round" className={up ? 'stroke-profit' : 'stroke-loss'} />
              </>
            )}
            {!comparing && (
            <>
            {/* Price pane */}
            {[model.pHi, model.pLo].map((v, i) => (
              <g key={`p${i}`}>
                <line x1={PAD.left} x2={W - PAD.right} y1={model.yP(v)} y2={model.yP(v)} className="stroke-chart-grid" strokeWidth={1} />
                <text x={PAD.left - 8} y={model.yP(v) + 4} textAnchor="end" className="fill-faint text-[11px]">
                  {v.toLocaleString('en-US', { maximumFractionDigits: v >= 100 ? 0 : 2 })}
                </text>
              </g>
            ))}
            <path d={model.priceLine} fill="none" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" className="stroke-accent" />

            {/* P&L pane */}
            {[model.qHi, model.qLo].filter((v, i, a) => v !== 0 && a.indexOf(v) === i).map((v) => (
              <g key={`q${v}`}>
                <line x1={PAD.left} x2={W - PAD.right} y1={model.yQ(v)} y2={model.yQ(v)} className="stroke-chart-grid" strokeWidth={1} />
                <text x={PAD.left - 8} y={model.yQ(v) + 4} textAnchor="end" className="fill-faint text-[11px]">
                  {money(v, { sign: true, cents: false })}
                </text>
              </g>
            ))}
            <line x1={PAD.left} x2={W - PAD.right} y1={model.yQ(0)} y2={model.yQ(0)} className="stroke-line-strong" strokeDasharray="3 3" />
            <text x={PAD.left - 8} y={model.yQ(0) + 4} textAnchor="end" className="fill-faint text-[11px]">
              $0
            </text>
            <path d={model.pnlLine} fill="none" strokeWidth={2} strokeLinejoin="round" className={up ? 'stroke-profit' : 'stroke-loss'} />
            </>
            )}

            {/* Shared time axis */}
            <text x={PAD.left} y={H - 3} className="fill-faint text-[11px]">
              {shortDate(first!.date)}
            </text>
            <text x={W - PAD.right} y={H - 3} textAnchor="end" className="fill-faint text-[11px]">
              {shortDate(last!.date)}
            </text>

            {shown && hover !== null && (
              <>
                <line x1={model.x(shown.t)} x2={model.x(shown.t)} y1={PAD.top} y2={H - PAD.bottom} className="stroke-line-strong" />
                <circle
                  cx={model.x(shown.t)}
                  cy={comparing ? model.yC(heldAt(hover) ?? 0) : model.yP(shown.close)}
                  r={4}
                  className="fill-accent stroke-raised-solid"
                  strokeWidth={2}
                />
                <circle
                  cx={model.x(shown.t)}
                  cy={comparing ? model.yC(shown.pnl) : model.yQ(shown.pnl)}
                  r={4}
                  className={`${shown.pnl >= 0 ? 'fill-profit' : 'fill-loss'} stroke-raised-solid`}
                  strokeWidth={2}
                />
              </>
            )}
          </svg>
        )}
      </div>
      <div className="mt-1 h-4 text-[12px] text-muted">
        {shown && first && comparing && hover !== null ? (
          <span className="num">
            {fullDate(shown.date)} · holding {money(heldAt(hover), { sign: true, cents: false })} · your trading{' '}
            {money(shown.pnl, { sign: true, cents: false })}
          </span>
        ) : comparing ? (
          <span className="text-faint">
            Blue: {history.data?.holding} bought on {first ? fullDate(first.date) : 'day one'} and held
            {history.data?.continuous ? ' (continuous front-month chart, so roll gaps are in it)' : ''}. Green: your
            running P&L.
          </span>
        ) : shown && first ? (
          <span className="num">
            {fullDate(shown.date)} · {name} {shown.close.toLocaleString('en-US', { maximumFractionDigits: 2 })} (
            {pct(shown.close / first.close - 1, 1, true)}) · running {money(shown.pnl, { sign: true, cents: false })}
          </span>
        ) : (
          <span className="text-faint">
            Top: {name}’s daily close{history.data?.continuous ? ' (continuous front-month chart)' : ''}. Bottom: your
            running P&L.
          </span>
        )}
      </div>
    </div>
  )
}
