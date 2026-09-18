import { useState } from 'react'
import { ErrorPanel, Loading, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, num, fullDate, decimals, EM_DASH } from '../lib/format'
import type { PairCandidate, PairLink, PairSide } from '../types'

const WEIGHT: Record<PairLink['weight'], string> = {
  strong: 'border-accent/40 bg-accent-soft text-accent',
  neutral: 'border-line bg-sunken text-muted',
  weak: 'border-line bg-sunken text-faint',
}

function Links({ links }: { links: PairLink[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {links.map((l) => (
        <span
          key={l.label}
          className={`rounded-sm border px-1.5 py-0.5 text-[11px] ${WEIGHT[l.weight]}`}
        >
          <span className="opacity-70">{l.label}:</span> {l.value}
        </span>
      ))}
    </div>
  )
}

function Side({ side }: { side: PairSide }) {
  return (
    <div className="rounded-sm border border-line bg-sunken/50 p-2.5">
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-medium">{side.structure}</span>
        {side.roll_count > 0 && (
          <span className="text-[10px] text-faint">rolled {side.roll_count}×</span>
        )}
        <span
          className={`num ml-auto text-xs font-medium ${
            (num(side.realized_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
          }`}
        >
          {money(side.realized_pnl, { sign: true, cents: false })}
        </span>
      </div>

      <table className="mt-1.5 w-full text-[11px]">
        <tbody className="num">
          {side.legs.map((leg, i) => (
            <tr key={i}>
              <td className="pr-2 text-muted">{leg.side === 'Short' ? 'short' : 'long'}</td>
              <td className="pr-2 text-right">{decimals(leg.quantity, 0)}</td>
              <td className="pr-2">
                {leg.right === 'shares'
                  ? 'shares'
                  : `${leg.strike ?? ''} ${leg.right === 'C' ? 'call' : 'put'}`}
              </td>
              <td className="pr-2 text-faint">{leg.expiration ? fullDate(leg.expiration) : ''}</td>
              <td className="pr-2 text-right text-faint">@{money(leg.open_price)}</td>
              <td className="text-right text-faint">
                {leg.delta === null ? '' : `Δ${decimals(leg.delta, 2)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <dl className="mt-1.5 grid grid-cols-2 gap-x-2 border-t border-line/60 pt-1.5 text-[10px] text-faint">
        <dt>Opened</dt>
        <dd className="text-right">{fullDate(side.opened_at)}</dd>
        <dt>Closed</dt>
        <dd className="text-right">
          {side.is_open ? 'still open' : fullDate(side.closed_at)}
        </dd>
        <dt>Held</dt>
        <dd className="text-right">{side.days_held === null ? EM_DASH : `${side.days_held}d`}</dd>
        <dt>DTE at entry</dt>
        <dd className="text-right">{side.dte_at_entry ?? EM_DASH}</dd>
        <dt>Credit</dt>
        <dd className="text-right">{money(side.credit, { sign: true, cents: false })}</dd>
      </dl>
    </div>
  )
}

/* The accuracy page. Everything that bears on "one trade or two" is on the
   card, because the question cannot be answered from a structure name. The
   strongest signal is not the shape at all: two positions opened a minute
   apart AND closed a minute apart were one decision. */
export function Grouping() {
  const { data, error, loading, reload } = useAsync(() => api.pairingCandidates(), [])
  const decided = useAsync(() => api.pairingDecisions(), [])
  const [busy, setBusy] = useState<string | null>(null)

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Looking for trades that belong together" />

  const questions = (data ?? []).filter((c) => c.first_of_pattern)

  async function decide(c: PairCandidate, decision: 'merge' | 'separate') {
    setBusy(c.pattern)
    try {
      await api.decidePairing(c.pattern, decision)
      reload()
      decided.reload()
    } finally {
      setBusy(null)
    }
  }

  async function undo(pattern: string) {
    setBusy(pattern)
    try {
      await api.undoPairing(pattern)
      reload()
      decided.reload()
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <SectionHeading
        title="Which legs belong together"
        hint="every answer is remembered as a shape, not a one-off"
      />

      {questions.length === 0 ? (
        <Empty title="Nothing waiting." hint="Every pair has been answered." />
      ) : (
        <div className="space-y-3">
          {questions.map((c) => (
            <div key={c.pattern} className="rounded-card border border-line bg-raised p-4">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-sm font-semibold">{c.underlying}</span>
                <span className="text-xs text-muted">
                  together they would be a <span className="text-ink">{c.would_become}</span>
                </span>
                <span
                  className={`num ml-auto text-sm font-medium ${
                    (num(c.combined_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
                  }`}
                >
                  {money(c.combined_pnl, { sign: true, cents: false })} combined
                </span>
              </div>

              <div className="mt-2">
                <Links links={c.links} />
              </div>

              <div className="mt-2.5 grid gap-2 sm:grid-cols-2">
                {c.sides.map((s) => (
                  <Side key={s.id} side={s} />
                ))}
              </div>

              {c.others_like_it > 0 && (
                <div className="mt-2 text-[11px] text-faint">
                  Your answer also settles {c.others_like_it} other pair
                  {c.others_like_it === 1 ? '' : 's'} shaped like this.
                </div>
              )}

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

      {(decided.data ?? []).length > 0 && (
        <div className="pt-2">
          <div className="mb-2 flex items-baseline gap-2">
            <h3 className="text-[13px] font-semibold text-muted">
              Answered ({(decided.data ?? []).length})
            </h3>
            <span className="text-[11px] text-faint">change any of these and the journal rebuilds</span>
          </div>
          <div className="overflow-hidden rounded-card border border-line bg-raised">
            {(decided.data ?? []).map((d) => (
              <div
                key={d.pattern}
                className="flex flex-wrap items-baseline gap-2 border-b border-line/60 px-3.5 py-2 last:border-0"
              >
                <span
                  className={`shrink-0 rounded-sm border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                    d.decision === 'merge'
                      ? 'border-accent/40 bg-accent-soft text-accent'
                      : 'border-line bg-sunken text-muted'
                  }`}
                >
                  {d.decision === 'merge' ? 'one trade' : 'separate'}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-muted">
                  {d.note ?? d.pattern}
                </span>
                <button
                  onClick={() => void undo(d.pattern)}
                  disabled={busy === d.pattern}
                  className="shrink-0 rounded-sm px-2 py-0.5 text-[11px] text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-50"
                >
                  {busy === d.pattern ? 'Rebuilding…' : 'Change'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
