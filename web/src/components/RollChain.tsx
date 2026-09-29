import { useState } from 'react'
import { api } from '../lib/api'
import { money, num, shortDate } from '../lib/format'
import type { RollStep } from '../types'

/* What a trade's rolls actually were.

   A row saying "rolled 4×" was the whole record. The rolls themselves lived in
   a line of prose in the notes — "rolled: absorbed 5WZ55394:/RTYZ6:500726191"
   — which cannot be read, drawn or taken back. Each roll is a decision, and a
   ladder of them is the shape of the trade: 3400 call, then 3300, then 3200,
   then 3150. That is worth seeing in one column.

   Each step names what it closed, what it opened in its place, and what the
   new legs took in. The arrow between them is the roll. And because the app
   reads a roll off the order ids rather than being told about one, each step
   can be taken back: "not a roll" splits it out and the two stand as separate
   trades through every rebuild after it. */
/* Two lots of the same contract closed by one order are two lines saying the
   same thing. Counted rather than repeated. */
function tally(lines: string[]): string[] {
  const seen = new Map<string, number>()
  for (const line of lines) seen.set(line, (seen.get(line) ?? 0) + 1)
  return [...seen].map(([line, n]) => (n > 1 ? `${line} ×${n}` : line))
}

export function RollChain({
  strategyId,
  rolls,
  rollCount,
  holding,
  onChange,
  onClose,
}: {
  strategyId: string
  rolls: RollStep[]
  rollCount: number
  /** What the trade holds now, one line per leg; null once it is closed. */
  holding: string[] | null
  onChange?: () => void
  onClose?: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [said, setSaid] = useState<string | null>(null)

  if (rolls.length === 0) {
    if (!rollCount) return null
    return (
      <div className="rounded-card border border-line bg-sunken p-4 text-[15px] text-muted">
        Rolled {rollCount}× before the app recorded what each roll was. The next sync rebuilds the
        chain from the ledger.
      </div>
    )
  }

  async function separate(absorbedId: string) {
    setBusy(absorbedId)
    setSaid(null)
    try {
      await api.decideRoll(strategyId, absorbedId, 'separate')
      setSaid('Split off. It stands as its own trade from the next sync.')
      onChange?.()
    } finally {
      setBusy(null)
    }
  }

  const total = rolls.reduce((acc, r) => acc + (num(r.credit) ?? 0), 0)

  /* Colour carries the reading: red is what the roll bought back, green is
     what it sold in its place, and the arrow between them is the roll. Each
     step sits on a white card with an accent edge so a ladder of five reads
     as five steps, not as one grey block. */
  return (
    <div className="rounded-card border-2 border-accent/50 bg-accent-soft/40 p-4">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
        <span className="text-[17px] font-semibold text-ink">
          {rolls.length} roll{rolls.length === 1 ? '' : 's'}
        </span>
        <span className="text-[16px] text-ink">
          took in{' '}
          <span className={`figure font-semibold ${total >= 0 ? 'text-profit' : 'text-loss'}`}>
            {money(total, { sign: true, cents: false })}
          </span>{' '}
          across them
        </span>
        {onClose && (
          <button
            onClick={onClose}
            className="ml-auto rounded-sm border border-line-strong bg-raised px-2.5 py-1 text-[14px] text-ink transition-colors hover:bg-hover"
          >
            Hide rolls
          </button>
        )}
      </div>

      {/* What is held now comes first and stands apart: it is the trade. The
          rolls under it are how it got here, newest first. */}
      {holding !== null && (
        <div className="mb-4 rounded-sm border-2 border-profit/50 bg-raised p-3.5 shadow-[var(--shadow-sm)]">
          <div className="text-[12px] font-semibold uppercase tracking-wider text-profit">
            Open now
          </div>
          <div className="mono mt-1 text-[15px] text-ink">
            {holding.length > 0 ? tally(holding).join(' · ') : 'nothing open'}
          </div>
        </div>
      )}

      <div className="mb-2 text-[13px] font-semibold uppercase tracking-wider text-muted">
        How it got here — latest roll first
      </div>
      <ol className="space-y-3">
        {rolls
          .map((r, i) => [r, i] as const)
          .reverse()
          .map(([r, i]) => {
          const credit = num(r.credit) ?? 0
          return (
            <li
              key={`${r.absorbed_id}-${i}`}
              className="rounded-sm border border-line border-l-4 border-l-accent bg-raised p-3.5 shadow-[var(--shadow-sm)]"
            >
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="num rounded-full bg-accent px-2 py-0.5 text-[13px] font-semibold text-white">
                  #{i + 1}
                </span>
                <span className="text-[16px] font-medium text-ink">{shortDate(r.at)}</span>
                <span className="num ml-auto text-[16px]">
                  <span className={`font-semibold ${credit >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {money(r.credit, { sign: true, cents: false })}
                  </span>{' '}
                  <span className="text-ink">{credit >= 0 ? 'taken in' : 'paid'}</span>
                </span>
              </div>

              {/* Out on the left, in on the right, the arrow between them. */}
              <div className="mt-2.5 grid items-stretch gap-2 sm:grid-cols-[1fr_auto_1fr]">
                <div className="rounded-sm border border-loss/30 bg-loss-soft px-3 py-2">
                  <div className="text-[12px] font-semibold uppercase tracking-wider text-loss">
                    Closed
                  </div>
                  <div className="mono text-[15px] text-ink">
                    {r.closed.length > 0 ? tally(r.closed).join(' · ') : 'nothing closed'}
                  </div>
                </div>
                <div aria-hidden className="flex items-center justify-center text-[24px] font-bold text-accent">
                  <span className="hidden sm:inline">→</span>
                  <span className="sm:hidden">↓</span>
                </div>
                <div className="rounded-sm border border-profit/30 bg-profit-soft px-3 py-2">
                  <div className="text-[12px] font-semibold uppercase tracking-wider text-profit">
                    Opened
                  </div>
                  <div className="mono text-[15px] text-ink">
                    {r.opened.length > 0 ? tally(r.opened).join(' · ') : 'nothing opened'}
                  </div>
                </div>
              </div>

              <div className="mt-2.5 flex flex-wrap items-baseline gap-3">
                <button
                  onClick={() => void separate(r.absorbed_id)}
                  disabled={busy === r.absorbed_id}
                  title="Keep these as two separate trades instead"
                  className="rounded-sm border border-tested/50 bg-tested-soft px-2.5 py-1 text-[14px] font-medium text-tested transition-colors hover:border-tested disabled:opacity-40"
                >
                  {busy === r.absorbed_id ? 'Splitting…' : 'Not a roll'}
                </button>
                {r.order_id !== null && (
                  <span className="num text-[13px] text-muted">order {r.order_id}</span>
                )}
              </div>
            </li>
          )
          })}
      </ol>

      {said && <div className="mt-2 text-[15px] font-medium text-accent">{said}</div>}
    </div>
  )
}

/* "rolled 3×", as the way in to the rolls: a button, not a note. */
export function RolledTag({
  count,
  shown,
  onClick,
}: {
  count: number
  shown: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      title={shown ? 'Hide the rolls' : 'Show each roll'}
      className={`ml-1 whitespace-nowrap rounded-sm border px-1.5 py-px text-[12px] font-medium transition-colors ${
        shown
          ? 'border-accent bg-accent text-white'
          : 'border-accent/50 bg-accent-soft text-accent hover:bg-accent hover:text-white'
      }`}
    >
      rolled {count}× {shown ? '▴' : '▾'}
    </button>
  )
}
