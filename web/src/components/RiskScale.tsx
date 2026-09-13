import { num, money, pct, EM_DASH } from '../lib/format'
import type { StrategyPnL } from '../types'

/* The single most important picture in this application.

   The user's complaint in one image: a defined-risk spread down 150% of the
   credit it collected is only a little over a third of the way toward its worst
   case, and this bar shows that at a glance. The scale runs from max loss on
   the left to max profit on the right, with a marker at the current P&L.

   For undefined risk there is no left-hand endpoint to draw, so the bar is
   deliberately open-ended rather than pretending a bound exists. Showing a
   fabricated floor for a naked short would be the same lie in a different form. */
export function RiskScale({ pnl }: { pnl: StrategyPnL }) {
  const openPnl = num(pnl.open_pnl)
  const maxProfit = num(pnl.max_profit)
  const maxLoss = num(pnl.max_loss)

  if (openPnl === null || maxProfit === null || maxProfit === 0) {
    return <div className="text-xs text-faint">{EM_DASH}</div>
  }

  const defined = maxLoss !== null && maxLoss > 0

  if (!defined) {
    // Open-ended: anchor the scale at 2x the credit, the user's own stop, and
    // let the marker run past the edge when the loss exceeds it.
    const floor = -2 * maxProfit
    const raw = (openPnl - floor) / (maxProfit - floor)
    const position = Math.max(0, Math.min(1, raw))
    const past = raw < 0
    return (
      <div className="w-full">
        <div className="relative h-2 w-full overflow-hidden rounded-full bg-sunken">
          <div className="absolute inset-y-0 left-0 w-px bg-line-strong" />
          <div className="absolute inset-y-0 left-1/3 w-px bg-line-strong" />
          <div
            className={`absolute top-1/2 h-3 w-1 -translate-y-1/2 rounded-full ${openPnl >= 0 ? 'bg-profit' : 'bg-loss'}`}
            style={{ left: `calc(${position * 100}% - 2px)` }}
          />
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-faint">
          <span>{past ? 'past 2x credit' : '2x credit stop'}</span>
          <span className="text-muted">undefined risk</span>
          <span>{money(maxProfit, { cents: false })}</span>
        </div>
      </div>
    )
  }

  const span = maxProfit + maxLoss!
  const position = Math.max(0, Math.min(1, (openPnl + maxLoss!) / span))
  const breakeven = maxLoss! / span

  return (
    <div className="w-full">
      <div className="relative h-2 w-full overflow-hidden rounded-full bg-sunken">
        <div
          className="absolute inset-y-0 left-0 bg-loss-soft"
          style={{ width: `${breakeven * 100}%` }}
        />
        <div
          className="absolute inset-y-0 bg-profit-soft"
          style={{ left: `${breakeven * 100}%`, right: 0 }}
        />
        <div className="absolute inset-y-0 w-px bg-line-strong" style={{ left: `${breakeven * 100}%` }} />
        <div
          className={`absolute top-1/2 h-3 w-1 -translate-y-1/2 rounded-full ${openPnl >= 0 ? 'bg-profit' : 'bg-loss'}`}
          style={{ left: `calc(${position * 100}% - 2px)` }}
        />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-faint">
        <span>-{money(maxLoss, { cents: false }).replace('$', '$')} max loss</span>
        <span className="text-muted">
          {pnl.pct_of_max_loss !== null && num(pnl.pct_of_max_loss)! > 0
            ? `${pct(pnl.pct_of_max_loss, 0)} of max loss`
            : 'breakeven'}
        </span>
        <span>{money(maxProfit, { cents: false })} max</span>
      </div>
    </div>
  )
}
