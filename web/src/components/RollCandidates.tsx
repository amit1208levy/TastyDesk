import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, num, shortDate } from '../lib/format'
import type { RollCandidate } from '../types'

const TONE: Record<RollCandidate['confidence'], string> = {
  high: 'border-accent/45 bg-accent-soft text-accent',
  likely: 'border-line-strong bg-sunken text-muted',
  possible: 'border-line bg-sunken text-muted',
}

/* Rolls done as two orders cannot be detected from the fills alone: closing one
   trade and opening another the same afternoon is ordinary behaviour, not
   necessarily a roll. So these are proposed rather than applied.

   It matters because an unlinked roll reads as a loser followed by an unrelated
   winner, which flatters the win rate and hides what rolling actually costs.

   Three things were wrong with how they were asked. The answer was not kept —
   "Separate" lived in this component's state and died with the page, so a
   backlog worked through last week came back in full. The card showed two
   opaque ids and a sentence, which is not enough to decide on. And "One trade"
   rebuilt the whole journal before the row would go away, so answering ninety
   of them meant ninety waits. */
export function RollCandidates({ onChange }: { onChange?: () => void }) {
  const { data, reload } = useAsync(() => api.rollCandidates(), [])
  // Answered in this session: removed from the list the instant it is clicked,
  // while the write to the database happens behind it. The answer is stored
  // either way, so this is only about what the eye sees.
  const [answered, setAnswered] = useState<Set<string>>(new Set())
  const [linking, setLinking] = useState(0)
  const [expanded, setExpanded] = useState(false)
  const [clearing, setClearing] = useState(false)

  const key = (c: RollCandidate) => `${c.closed_id}|${c.opened_id}`
  const pending = (data ?? []).filter((c) => !answered.has(key(c)))

  if (pending.length === 0) return null

  function answer(c: RollCandidate) {
    setAnswered((d) => new Set(d).add(key(c)))
  }

  function link(c: RollCandidate) {
    answer(c)
    setLinking((n) => n + 1)
    // Not awaited. Rebuilding the journal takes seconds and the answer does
    // not depend on it: the row goes now, the work finishes behind the button.
    void api
      .linkStrategies([c.closed_id, c.opened_id])
      .then(() => onChange?.())
      .finally(() => setLinking((n) => n - 1))
  }

  function separate(c: RollCandidate) {
    answer(c)
    void api.decideRoll(c.closed_id, c.opened_id, 'separate')
  }

  async function separateAll() {
    setClearing(true)
    try {
      await api.separateAllRolls()
      reload()
      setAnswered(new Set())
    } finally {
      setClearing(false)
    }
  }

  const shown = expanded ? pending : pending.slice(0, 3)

  return (
    <div className="sheened rounded-card border border-line bg-raised p-5 shadow-[var(--shadow-sm)]">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="display text-[19px]">
          {pending.length} possible roll{pending.length === 1 ? '' : 's'}
        </h3>
        <span className="text-[14px] text-muted">
          executed as two orders, so they were not linked automatically
        </span>
        {linking > 0 && (
          <span className="num text-[13px] text-accent">
            linking {linking}… the list does not wait
          </span>
        )}
        <button
          onClick={() => void separateAll()}
          disabled={clearing}
          className="ml-auto rounded-sm border border-line px-3 py-1 text-[13px] text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40"
        >
          {clearing ? 'Marking…' : `Separate all ${pending.length}`}
        </button>
      </div>
      <p className="mt-1.5 text-[15px] leading-relaxed text-muted">
        Left apart, a roll reads as a loser followed by an unrelated winner — which flatters your win
        rate and hides what rolling costs you. Every answer is remembered.
      </p>

      <ul className="mt-4 space-y-3">
        {shown.map((c) => {
          const pnl = num(c.closed_pnl)
          return (
            <li key={key(c)} className="rounded-card border border-line bg-sunken px-4 py-3.5">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span
                  className={`shrink-0 rounded-full border px-2.5 py-1 text-[12px] font-medium uppercase leading-none tracking-[0.12em] ${TONE[c.confidence]}`}
                >
                  {c.confidence}
                </span>
                <span className="text-[17px] font-semibold">{c.underlying}</span>
                <span className="text-[15px] text-ink">{c.reason}</span>
              </div>

              {/* Both sides, spelled out. The decision is whether these are one
                  trade, and that cannot be read off two strategy ids. */}
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <div className="label">
                    Closed{c.closed_at ? ` · ${shortDate(c.closed_at)}` : ''}
                  </div>
                  <ul className="mt-1 space-y-0.5 text-[15px]">
                    {c.closed_legs.map((leg) => (
                      <li key={leg} className="num text-ink">
                        {leg}
                      </li>
                    ))}
                  </ul>
                  <div className="mt-1.5 text-[14px] text-muted">
                    took in{' '}
                    <span className="figure text-ink">
                      {money(c.closed_credit, { sign: true, cents: false })}
                    </span>
                    , realised{' '}
                    <span
                      className={`figure ${pnl !== null && pnl < 0 ? 'text-loss' : 'text-profit'}`}
                    >
                      {money(c.closed_pnl, { sign: true, cents: false })}
                    </span>
                    {c.days_held !== null && ` after ${c.days_held}d`}
                  </div>
                </div>

                <div>
                  <div className="label">
                    Opened{c.opened_at ? ` · ${shortDate(c.opened_at)}` : ''}
                    {c.gap_minutes < 60 ? ` · ${c.gap_minutes} min later` : ''}
                  </div>
                  <ul className="mt-1 space-y-0.5 text-[15px]">
                    {c.opened_legs.map((leg) => (
                      <li key={leg} className="num text-ink">
                        {leg}
                      </li>
                    ))}
                  </ul>
                  <div className="mt-1.5 text-[14px] text-muted">
                    took in{' '}
                    <span className="figure text-ink">
                      {money(c.opened_credit, { sign: true, cents: false })}
                    </span>
                  </div>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
                <button
                  onClick={() => link(c)}
                  className="rounded-sm border border-accent/50 bg-accent-soft px-3.5 py-1.5 text-[14px] text-accent transition-colors hover:bg-accent/10"
                >
                  One trade
                </button>
                <button
                  onClick={() => separate(c)}
                  className="rounded-sm border border-line-strong px-3.5 py-1.5 text-[14px] text-muted transition-colors hover:bg-hover hover:text-ink"
                >
                  Separate
                </button>
                <span className="num self-center text-[12px] text-faint">
                  {c.closed_id} + {c.opened_id}
                </span>
              </div>
            </li>
          )
        })}
      </ul>

      {pending.length > 3 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-3 text-[14px] text-muted transition-colors hover:text-ink"
        >
          {expanded ? 'Show fewer' : `Show ${pending.length - 3} more`}
        </button>
      )}
    </div>
  )
}
