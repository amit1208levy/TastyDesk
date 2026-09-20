import { money, num } from '../lib/format'
import type { StrategyView } from '../types'

const TONE: Record<string, string> = {
  act: 'border-danger/45 bg-danger-soft text-danger',
  take: 'border-profit/45 bg-profit-soft text-profit',
  watch: 'border-tested/40 bg-tested-soft text-tested',
}

/* What to do today, named.

   The tile above says "5 to decide", which is a count, and a count is a dead
   end: it cannot be clicked, and it does not say which five. This does. Each
   line is a position, the instruction your own rules produce for it, and the
   reason — and clicking one takes you to its row. */
export function Decisions({
  views,
  onPick,
}: {
  views: StrategyView[]
  onPick: (id: string) => void
}) {
  const needed = views
    .filter((v) => v.verdict && v.verdict.tone !== 'none' && v.verdict.tone !== 'watch')
    .sort((a, b) => (a.verdict?.rank ?? 9) - (b.verdict?.rank ?? 9))

  if (needed.length === 0) {
    return (
      <section className="surface sheened px-6 py-5">
        <div className="label text-accent">Today</div>
        <p className="mt-1.5 text-[17px]">
          Nothing needs a decision. Every position is inside the rules you set.
        </p>
      </section>
    )
  }

  return (
    <section className="surface sheened overflow-hidden">
      <div className="flex flex-wrap items-baseline gap-3 px-6 pt-5">
        <div className="label text-accent">Needs a decision</div>
        <span className="num text-[13px] text-muted">
          {needed.length} of {views.length} positions
        </span>
      </div>

      <ul className="mt-3 divide-y divide-line">
        {needed.map((v) => {
          const pnl = num(v.pnl.open_pnl)
          return (
            <li key={v.strategy.id}>
              <button
                onClick={() => onPick(v.strategy.id)}
                className="flex w-full flex-wrap items-baseline gap-x-3 gap-y-1 px-6 py-3 text-left hover:bg-hover"
              >
                <span
                  className={`shrink-0 rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase leading-none tracking-[0.12em] ${
                    TONE[v.verdict!.tone] ?? ''
                  }`}
                >
                  {v.verdict!.action}
                </span>
                <span className="text-[17px] font-semibold">{v.strategy.underlying}</span>
                <span className="text-[15px] text-muted">
                  {v.named_name ?? v.strategy.strategy_type}
                </span>
                <span className="flex-1 truncate text-[14px] text-faint">{v.verdict!.reason}</span>
                <span
                  className={`figure shrink-0 text-[16px] font-semibold ${
                    pnl === null ? 'text-faint' : pnl >= 0 ? 'text-profit' : 'text-loss'
                  }`}
                >
                  {money(v.pnl.open_pnl, { sign: true, cents: false })}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
