import { createContext, useContext } from 'react'
import type { StrategyView } from '../types'

/* Putting one leg in a different trade. The Positions table provides this; a
   page that does not (the strategy page) shows the legs without the control. */
export type DraggedLeg = { symbol: string; fromId: string }

export type LegMover = {
  /** The trades a leg of this row could go to: every other open row. */
  targets: (fromId: string) => { id: string; label: string }[]
  /** Move the leg; a null destination makes it a trade of its own. */
  move: (symbol: string, fromId: string, toId: string | null) => Promise<void>
  dragged: DraggedLeg | null
  setDragged: (leg: DraggedLeg | null) => void
}

export const LegMoveContext = createContext<LegMover | null>(null)

export function useLegMover(): LegMover | null {
  return useContext(LegMoveContext)
}

/** "Dec 31" from an ISO date, or null. */
export function shortDay(iso: unknown): string | null {
  if (typeof iso !== 'string' || iso.length < 10) return null
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/** What a row is called when it is one choice among several. */
export function rowLabel(v: StrategyView): string {
  const name = v.named_name ?? v.strategy.strategy_type
  const exp = shortDay((v.values ?? {}).expiry)
  return `${v.strategy.underlying} · ${name}${exp ? ` · exp ${exp}` : ''}`
}

/** Rows that share a ticker and a name, which need the expiry to tell them apart. */
export function twinsOf(views: StrategyView[]): Set<string> {
  const seen = new Map<string, number>()
  for (const v of views) {
    const k = `${v.strategy.underlying}|${v.named_name ?? v.strategy.strategy_type}`
    seen.set(k, (seen.get(k) ?? 0) + 1)
  }
  return new Set(
    views
      .filter((v) => (seen.get(`${v.strategy.underlying}|${v.named_name ?? v.strategy.strategy_type}`) ?? 0) > 1)
      .map((v) => v.strategy.id),
  )
}
