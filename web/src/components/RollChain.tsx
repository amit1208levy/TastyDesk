import { useState } from 'react'
import { api } from '../lib/api'
import { money, num, shortDate } from '../lib/format'
import type { Strategy } from '../types'

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

export function RollChain({ strategy, onChange }: { strategy: Strategy; onChange?: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const rolls = strategy.rolls ?? []

  if (rolls.length === 0) {
    if (!strategy.roll_count) return null
    return (
      <div className="rounded-card border border-line bg-sunken p-4 text-[15px] text-muted">
        Rolled {strategy.roll_count}× before the app recorded what each roll was. The next sync
        rebuilds the chain from the ledger.
      </div>
    )
  }

  async function separate(absorbedId: string) {
    setBusy(absorbedId)
    setSaid(null)
    try {
      await api.decideRoll(strategy.id, absorbedId, 'separate')
      setSaid('Split off. It stands as its own trade from the next sync.')
      onChange?.()
    } finally {
      setBusy(null)
    }
  }

  const total = rolls.reduce((acc, r) => acc + (num(r.credit) ?? 0), 0)

  return (
    <div className="rounded-card border border-line bg-sunken p-4">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
        <span className="label text-[13px]">
          {rolls.length} roll{rolls.length === 1 ? '' : 's'}
        </span>
        <span className="text-[15px] text-muted">
          took in{' '}
          <span className={`figure ${total >= 0 ? 'text-profit' : 'text-loss'}`}>
            {money(total, { sign: true, cents: false })}
          </span>{' '}
          across them
        </span>
      </div>

      <ol className="space-y-2.5">
        {rolls.map((r, i) => (
          <li key={`${r.absorbed_id}-${i}`} className="rounded-sm border border-line bg-raised p-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="num shrink-0 text-[13px] text-faint">#{i + 1}</span>
              <span className="text-[15px] text-muted">{shortDate(r.at)}</span>
              <span className="num ml-auto text-[15px]">
                <span className={(num(r.credit) ?? 0) >= 0 ? 'text-profit' : 'text-loss'}>
                  {money(r.credit, { sign: true, cents: false })}
                </span>{' '}
                <span className="text-muted">taken in</span>
              </span>
            </div>

            {/* The roll itself: out on the left, in on the right, the arrow
                between them. Stacked on a narrow panel rather than squeezed. */}
            <div className="mt-2 grid items-center gap-x-3 gap-y-1 sm:grid-cols-[1fr_auto_1fr]">
              <div className="mono text-[15px] text-loss">
                {r.closed.length > 0 ? tally(r.closed).join(' · ') : 'nothing closed'}
              </div>
              <div aria-hidden className="hidden text-[18px] text-accent sm:block">
                →
              </div>
              <div className="mono text-[15px] text-profit">
                {r.opened.length > 0 ? tally(r.opened).join(' · ') : 'nothing opened'}
              </div>
            </div>

            <div className="mt-2 flex flex-wrap items-baseline gap-3">
              <button
                onClick={() => void separate(r.absorbed_id)}
                disabled={busy === r.absorbed_id}
                title="Keep these as two separate trades instead"
                className="rounded-sm border border-line-strong px-2.5 py-1 text-[14px] text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40"
              >
                {busy === r.absorbed_id ? 'Splitting…' : 'Not a roll'}
              </button>
              {r.order_id !== null && (
                <span className="num text-[13px] text-faint">order {r.order_id}</span>
              )}
            </div>
          </li>
        ))}
      </ol>

      {said && <div className="mt-2 text-[15px] text-accent">{said}</div>}
    </div>
  )
}
