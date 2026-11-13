import type { StrategyView } from '../types'

/* A decision he set aside stays set aside wherever the position shows: muted,
   sorted with the quiet rows, never in red. Hiding it in the decision list and
   leaving "Roll or close" in red on the row beneath was the app arguing with
   him. The hide is tied to the advice itself, so new advice comes back. */
export function setAside(views: StrategyView[], ignored: Record<string, string>): StrategyView[] {
  return views.map((v) =>
    v.verdict && ignored[v.strategy.id] === v.verdict.action
      ? {
          ...v,
          verdict: {
            ...v.verdict,
            tone: 'none',
            // Below every live verdict, "leave it" included.
            rank: 8,
            reason: `Set aside by you · ${v.verdict.reason}`,
            set_aside: true,
          },
        }
      : v,
  )
}
