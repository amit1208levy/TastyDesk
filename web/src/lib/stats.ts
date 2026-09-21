import type { NamedMember } from '../types'
import { num } from './format'

/* Performance recomputed in the browser.

   Normally a number the user reads should come from one place, and for every
   other page in this app it does. The confidence slider is the exception: it
   changes which trades are in a strategy on every drag, and a round trip per
   pixel would make the control feel broken. So the same arithmetic lives here,
   deliberately mirroring `analytics.performance`, and it must keep mirroring it:

   * only closed trades count — an open one has no result yet
   * expectancy is (win rate x average win) - (loss rate x average loss)
   * capture is measured over winners only, because a loser has no max profit
     it failed to reach; averaging its -2,190% turns the headline to noise */

export interface Stats {
  trades: number
  wins: number
  losses: number
  scratches: number
  winRate: number | null
  avgWin: number
  avgLoss: number
  expectancy: number
  total: number
  profitFactor: number | null
  avgDays: number | null
  capture: number | null
  largestWin: number | null
  largestLoss: number | null
}

const EMPTY: Stats = {
  trades: 0,
  wins: 0,
  losses: 0,
  scratches: 0,
  winRate: null,
  avgWin: 0,
  avgLoss: 0,
  expectancy: 0,
  total: 0,
  profitFactor: null,
  avgDays: null,
  capture: null,
  largestWin: null,
  largestLoss: null,
}

const mean = (xs: number[]): number | null =>
  xs.length === 0 ? null : xs.reduce((a, b) => a + b, 0) / xs.length

export function statsOf(rows: NamedMember[]): Stats {
  const closed = rows.filter((r) => !r.is_open)
  if (closed.length === 0) return EMPTY

  const pnls = closed.map((r) => num(r.realized_pnl) ?? 0)
  const wins = pnls.filter((p) => p > 0)
  const losses = pnls.filter((p) => p < 0)
  const n = pnls.length

  const grossProfit = wins.reduce((a, b) => a + b, 0)
  const grossLoss = -losses.reduce((a, b) => a + b, 0)
  const avgWin = wins.length ? grossProfit / wins.length : 0
  const avgLoss = losses.length ? grossLoss / losses.length : 0

  const captured = closed
    .filter((r) => (num(r.realized_pnl) ?? 0) > 0 && r.captured !== null)
    .map((r) => num(r.captured) ?? 0)

  const held = closed.map((r) => r.days_held).filter((d): d is number => d !== null)

  return {
    trades: n,
    wins: wins.length,
    losses: losses.length,
    scratches: n - wins.length - losses.length,
    winRate: wins.length / n,
    avgWin,
    avgLoss,
    expectancy: (wins.length / n) * avgWin - (losses.length / n) * avgLoss,
    total: pnls.reduce((a, b) => a + b, 0),
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : null,
    avgDays: mean(held),
    capture: mean(captured),
    largestWin: wins.length ? Math.max(...wins) : null,
    largestLoss: losses.length ? Math.min(...losses) : null,
  }
}

/** What the open trades are worth right now. Never mixed into the stats. */
export interface OpenNow {
  count: number
  pnl: number
  /** True when at least one open trade has no mark, so the total is partial. */
  partial: boolean
}

export function openNow(rows: NamedMember[]): OpenNow {
  const open = rows.filter((r) => r.is_open)
  return {
    count: open.length,
    pnl: open.reduce((total, r) => total + (num(r.open_pnl) ?? 0), 0),
    partial: open.some((r) => num(r.open_pnl) === null),
  }
}

export interface CurvePoint {
  date: string
  pnl: number
  cumulative: number
  id: string
  /** Not yet realized: this point is a mark, not a result. */
  open?: boolean
}

/* Cumulative P&L: realized in close order, then what is still open.

   The open trades used to be missing from the line entirely, which made a
   strategy with one closed trade and three running ones look like a strategy
   that had stopped. They are on it now, carried on from the last close in the
   order they were opened, and the chart draws that stretch differently because
   it is a different kind of number: a mark that can still move, not a result
   that is banked. */
export function equityCurve(rows: NamedMember[]): CurvePoint[] {
  const closed = rows
    .filter((r) => !r.is_open && r.closed)
    .sort((a, b) => (a.closed! < b.closed! ? -1 : a.closed! > b.closed! ? 1 : 0))

  let running = 0
  const out: CurvePoint[] = closed.map((r) => {
    const pnl = num(r.realized_pnl) ?? 0
    running += pnl
    return { date: r.closed!, pnl, cumulative: running, id: r.id }
  })

  const open = rows
    .filter((r) => r.is_open)
    .sort((a, b) => (a.opened < b.opened ? -1 : a.opened > b.opened ? 1 : 0))

  for (const r of open) {
    const pnl = num(r.open_pnl)
    // An unpriced trade cannot be added to the line. Skipping it keeps the
    // line honest; the count underneath says one is missing.
    if (pnl === null) continue
    running += pnl
    out.push({ date: r.opened, pnl, cumulative: running, id: r.id, open: true })
  }

  return out
}
