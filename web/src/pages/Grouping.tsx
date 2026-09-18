import { useState } from 'react'
import { ErrorPanel, Loading, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, num } from '../lib/format'
import type { PairCandidate } from '../types'

/* The accuracy page.

   The broker groups legs it filled under one order. Everything legged in — sell
   the call, sell the put a minute later — arrives as two trades where one
   decision was made, and it comes apart in a particular way: the short leg
   banks its credit and reads as a winner, the long leg was never meant to make
   money alone and reads as a loser. That is how "naked calls lose money" can be
   an artifact of bookkeeping rather than a fact about trading.

   Unmistakable pairs are merged without asking. These are the ones where the
   honest answer is "only you know", so they are asked rather than guessed — and
   each answer is remembered as a shape, so a handful of decisions settle
   hundreds of trades. */
export function Grouping() {
  const { data, error, loading, reload } = useAsync(() => api.pairingCandidates(), [])
  const [busy, setBusy] = useState<string | null>(null)

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Looking for trades that belong together" />

  const questions = (data ?? []).filter((c) => c.first_of_pattern)

  async function decide(c: PairCandidate, decision: 'merge' | 'separate') {
    setBusy(c.pattern)
    try {
      await api.decidePairing(c.pattern, decision)
      reload()
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <SectionHeading
        title="Which legs belong together"
        hint="every answer is remembered as a shape, not a one-off"
      />

      <div className="rounded-card border border-line bg-raised px-4 py-3 text-xs text-muted">
        Your broker groups legs it filled under one order. Anything you legged into arrives as two
        trades where you made one decision — and the short leg then reads as a winner while the long
        leg reads as a loser, which is bookkeeping, not trading. Matched strangles and verticals
        opened within minutes are merged automatically. These are the ones only you can settle.
      </div>

      {questions.length === 0 ? (
        <Empty
          title="Nothing waiting."
          hint="Every pair the journal could not settle on its own has been answered."
        />
      ) : (
        <div className="space-y-3">
          {questions.map((c) => (
            <div key={c.pattern} className="rounded-card border border-line bg-raised p-4">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-sm font-semibold">{c.underlying}</span>
                <span className="text-xs text-muted">
                  looks like {c.kind} — together they would be a{' '}
                  <span className="text-ink">{c.would_become}</span>
                </span>
                <span
                  className={`num ml-auto text-sm font-medium ${
                    (num(c.combined_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
                  }`}
                >
                  {money(c.combined_pnl, { sign: true, cents: false })}
                </span>
              </div>

              <div className="mt-2 space-y-1">
                {c.sides.map((s) => (
                  <div key={s.id} className="flex items-baseline gap-2 text-xs">
                    <span className="w-36 shrink-0 text-muted">{s.structure}</span>
                    <span className="mono flex-1 text-faint">{s.legs.join('; ')}</span>
                    <span
                      className={`num shrink-0 ${
                        (num(s.realized_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
                      }`}
                    >
                      {money(s.realized_pnl, { sign: true, cents: false })}
                    </span>
                  </div>
                ))}
              </div>

              <div className="mt-2 text-[11px] text-faint">
                Opened {c.gap_minutes < 1 ? 'in the same minute' : `${c.gap_minutes} minutes apart`}
                {c.others_like_it > 0 && (
                  <> · your answer also settles {c.others_like_it} other pair{c.others_like_it === 1 ? '' : 's'} like this</>
                )}
              </div>

              <div className="mt-2.5 flex gap-2 border-t border-line pt-2.5">
                <button
                  onClick={() => void decide(c, 'merge')}
                  disabled={busy === c.pattern}
                  className="rounded-sm border border-accent/50 bg-accent-soft px-3 py-1 text-xs text-accent transition-colors hover:bg-accent/10 disabled:opacity-50"
                >
                  {busy === c.pattern ? 'Rebuilding…' : 'One trade'}
                </button>
                <button
                  onClick={() => void decide(c, 'separate')}
                  disabled={busy === c.pattern}
                  className="rounded-sm border border-line-strong px-3 py-1 text-xs transition-colors hover:bg-hover disabled:opacity-50"
                >
                  Two separate trades
                </button>
                <span className="mono ml-auto self-center text-[10px] text-faint">{c.pattern}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
