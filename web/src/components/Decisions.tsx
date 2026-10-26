import { useState } from 'react'
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
  ignored,
  onIgnored,
}: {
  views: StrategyView[]
  onPick: (id: string) => void
  /** Strategy id to the action he set aside. A new action brings it back. */
  ignored: Record<string, string>
  onIgnored: (next: Record<string, string>) => void
}) {
  const [showHidden, setShowHidden] = useState(false)
  const all = views
    .filter((v) => v.verdict && v.verdict.tone !== 'none' && v.verdict.tone !== 'watch')
    .sort((a, b) => (a.verdict?.rank ?? 9) - (b.verdict?.rank ?? 9))
  const isHidden = (v: StrategyView) => ignored[v.strategy.id] === v.verdict!.action
  const needed = all.filter((v) => !isHidden(v))
  const hidden = all.filter(isHidden)

  function ignore(v: StrategyView) {
    onIgnored({ ...ignored, [v.strategy.id]: v.verdict!.action })
  }
  function restore(v: StrategyView) {
    const next = { ...ignored }
    delete next[v.strategy.id]
    onIgnored(next)
  }

  const footer =
    hidden.length > 0 ? (
      <div className="border-t border-line">
        <button
          onClick={() => setShowHidden(!showHidden)}
          className="flex w-full items-baseline gap-2 px-6 py-2.5 text-left text-[13px] text-muted hover:bg-hover hover:text-ink"
        >
          <span>{showHidden ? '▾' : '▸'}</span>
          <span>Hidden</span>
          <span className="num">{hidden.length}</span>
        </button>
        {showHidden && (
          <ul className="divide-y divide-line opacity-60">
            {hidden.map((v) => (
              <Line key={v.strategy.id} v={v} onPick={onPick} label="Show" onAside={() => restore(v)} />
            ))}
          </ul>
        )}
      </div>
    ) : null

  if (needed.length === 0) {
    return (
      <section className="surface sheened overflow-hidden">
        <div className="px-6 py-5">
          <div className="label text-accent">Today</div>
          <p className="mt-1.5 text-[17px]">
            {hidden.length > 0
              ? 'Nothing else needs a decision. The rest you have set aside.'
              : 'Nothing needs a decision. Every position is inside the rules you set.'}
          </p>
        </div>
        {footer}
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
        {needed.map((v) => (
          <Line key={v.strategy.id} v={v} onPick={onPick} label="Ignore" onAside={() => ignore(v)} />
        ))}
      </ul>
      {footer}
    </section>
  )
}

/** One position: click to go to its row; the button on hover sets it aside or back. */
function Line({
  v,
  onPick,
  label,
  onAside,
}: {
  v: StrategyView
  onPick: (id: string) => void
  label: string
  onAside: () => void
}) {
  const pnl = num(v.pnl.open_pnl)
  return (
    <li className="group relative flex items-stretch hover:bg-hover">
      <button
        onClick={() => onPick(v.strategy.id)}
        className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-3 gap-y-1 py-3 pl-6 pr-3 text-left"
      >
        <span
          className={`shrink-0 rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase leading-none tracking-[0.12em] ${
            TONE[v.verdict!.tone] ?? ''
          }`}
        >
          {v.verdict!.action}
        </span>
        <span className="text-[17px] font-semibold">{v.strategy.underlying}</span>
        <span className="text-[15px] font-medium text-muted">
          {v.named_name ?? v.strategy.strategy_type}
        </span>
        <span className="flex-1 truncate text-[15px] text-muted">{v.verdict!.reason}</span>
        <span
          className={`figure shrink-0 text-[16px] font-semibold ${
            pnl === null ? 'text-faint' : pnl >= 0 ? 'text-profit' : 'text-loss'
          }`}
        >
          {money(v.pnl.open_pnl, { sign: true, cents: false })}
        </span>
      </button>
      <button
        onClick={onAside}
        title={
          label === 'Ignore'
            ? 'Fine as it is: move it to Hidden. It comes back if the advice changes.'
            : 'Put it back in the list.'
        }
        className="mr-4 shrink-0 self-center rounded-sm border border-line px-2.5 py-1 text-[12px] text-muted opacity-0 transition-opacity hover:text-ink focus:opacity-100 group-hover:opacity-100"
      >
        {label}
      </button>
    </li>
  )
}
