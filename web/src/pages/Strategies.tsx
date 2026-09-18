import { useState } from 'react'
import { ErrorPanel, Loading, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, decimals, fullDate, num } from '../lib/format'
import type { NamedStrategy, StrategyMatch } from '../types'

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-faint">{label}</div>
      <div className={`num text-sm font-medium ${tone ?? ''}`}>{value}</div>
    </div>
  )
}

function MatchRow({
  match,
  picked,
  onToggle,
}: {
  match: StrategyMatch
  picked: boolean
  onToggle: () => void
}) {
  return (
    <li
      onClick={onToggle}
      className={`flex cursor-pointer items-baseline gap-2 border-t border-line/60 px-2 py-1.5 text-xs ${
        picked ? 'bg-accent-soft' : 'hover:bg-hover'
      }`}
    >
      <input type="checkbox" checked={picked} readOnly className="pointer-events-none" />
      <span className="w-20 shrink-0 text-muted">{fullDate(match.opened)}</span>
      <span className="mono flex-1 truncate text-faint">{match.legs.join('; ')}</span>
      <span className="w-12 shrink-0 text-right text-faint">{pct(match.score, 0)}</span>
      <span
        className={`num w-20 shrink-0 text-right ${
          (num(match.realized_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
        }`}
      >
        {money(match.realized_pnl, { sign: true, cents: false })}
      </span>
    </li>
  )
}

function StrategyCard({ strategy, onChange }: { strategy: NamedStrategy; onChange: () => void }) {
  const [open, setOpen] = useState(false)
  const matches = useAsync(() => (open ? api.strategyMatches(strategy.id) : Promise.resolve(null)), [
    open,
    strategy.id,
  ])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)

  const p = strategy.performance

  async function adopt() {
    if (picked.size === 0) return
    setBusy(true)
    try {
      await api.adoptMatches(strategy.id, Array.from(picked))
      setPicked(new Set())
      matches.reload()
      onChange()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-card border border-line bg-raised p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-sm font-semibold">{strategy.name}</h3>
        <span className="rounded-full border border-line px-1.5 py-0.5 text-[10px] text-muted">
          {strategy.product}
        </span>
        <span className="text-xs text-faint">{strategy.shape}</span>
        <button
          onClick={() => setOpen(!open)}
          className="ml-auto text-[11px] text-muted hover:text-ink"
        >
          {open ? 'Hide history' : 'Find older trades'}
        </button>
      </div>

      {strategy.name_reading && (
        <p className="mt-1 text-[11px] text-faint">
          Your name reads like {strategy.name_reading} — shown for your benefit; matching uses the
          legs and timing only.
        </p>
      )}

      <div className="mt-3 grid grid-cols-3 gap-3 border-t border-line pt-3 sm:grid-cols-6">
        <Stat label="Trades" value={String(p.trades)} />
        <Stat label="Win rate" value={p.win_rate === null ? '—' : pct(p.win_rate, 0)} />
        <Stat
          label="Total P&L"
          value={money(p.total_pnl, { sign: true, cents: false })}
          tone={(num(p.total_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'}
        />
        <Stat
          label="Expectancy"
          value={money(p.expectancy, { sign: true, cents: false })}
          tone={(num(p.expectancy) ?? 0) >= 0 ? 'text-profit' : 'text-loss'}
        />
        <Stat label="Avg days" value={decimals(p.avg_days_in_trade, 1)} />
        <Stat
          label="Captured"
          value={
            p.avg_pct_of_max_profit_captured === null
              ? '—'
              : pct(p.avg_pct_of_max_profit_captured, 0)
          }
        />
      </div>

      {open && (
        <div className="mt-3 border-t border-line pt-3">
          {matches.loading && !matches.data ? (
            <Loading label="Looking back through your history" />
          ) : !matches.data ? null : (
            <>
              <div className="flex items-baseline gap-2">
                <span className="text-[11px] font-medium uppercase tracking-wider text-faint">
                  {matches.data.confident.length} confident
                </span>
                <span className="text-[11px] text-faint">
                  same product, same legs, same expiry pattern — {pct(matches.data.threshold, 0)}+
                  shape match
                </span>
                <span className="num ml-auto text-xs text-muted">
                  {money(matches.data.confident_pnl, { sign: true, cents: false })} combined
                </span>
              </div>

              {matches.data.confident.length > 0 && (
                <ul className="mt-1.5">
                  {matches.data.confident.map((m) => (
                    <MatchRow
                      key={m.trade_id}
                      match={m}
                      picked={picked.has(m.trade_id)}
                      onToggle={() =>
                        setPicked((old) => {
                          const next = new Set(old)
                          if (next.has(m.trade_id)) next.delete(m.trade_id)
                          else next.add(m.trade_id)
                          return next
                        })
                      }
                    />
                  ))}
                </ul>
              )}

              {matches.data.review.length > 0 && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-[11px] text-faint">
                    {matches.data.review.length} close but not sure — nothing counted unless you say so
                  </summary>
                  <ul className="mt-1">
                    {matches.data.review.map((m) => (
                      <li key={m.trade_id} className="border-t border-line/60 px-2 py-1.5 text-xs">
                        <div className="flex items-baseline gap-2">
                          <span className="w-20 shrink-0 text-muted">{fullDate(m.opened)}</span>
                          <span className="mono flex-1 truncate text-faint">{m.legs.join('; ')}</span>
                          <span className="w-12 shrink-0 text-right text-faint">
                            {pct(m.score, 0)}
                          </span>
                        </div>
                        <div className="mt-0.5 pl-[5.5rem] text-[10px] text-faint">
                          {m.misses.join('; ')}
                        </div>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {picked.size > 0 && (
                <button
                  onClick={() => void adopt()}
                  disabled={busy}
                  className="mt-2 rounded-sm border border-accent/50 bg-accent-soft px-3 py-1 text-xs text-accent disabled:opacity-50"
                >
                  {busy ? 'Adding…' : `Add ${picked.size} to "${strategy.name}"`}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export function Strategies() {
  const { data, error, loading, reload } = useAsync(() => api.namedStrategies(), [])

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Reading your strategies" />

  return (
    <div className="space-y-3">
      <SectionHeading
        title="Your strategies"
        hint="what you named, and every older trade shaped the same way"
      />

      {(data ?? []).length === 0 ? (
        <Empty
          title="You have not named any strategies yet."
          hint="Go to Legs, pick the legs that belong to one idea, and name it. The app will then look back through your history for trades shaped the same way."
        />
      ) : (
        (data ?? []).map((s) => <StrategyCard key={s.id} strategy={s} onChange={reload} />)
      )}
    </div>
  )
}
