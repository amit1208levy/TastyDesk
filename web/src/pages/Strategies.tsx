import { useState } from 'react'
import { ErrorPanel, Loading, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, decimals, fullDate, shortDate, num } from '../lib/format'
import type { NamedMember, NamedStrategy, StrategyMatch } from '../types'

const ENDING: Record<NamedMember['ending'], { label: string; cls: string }> = {
  open: { label: 'open', cls: 'border-accent/40 bg-accent-soft text-accent' },
  closed: { label: 'closed', cls: 'border-line bg-sunken text-muted' },
  expired: { label: 'expired', cls: 'border-line bg-sunken text-muted' },
  assigned: { label: 'assigned', cls: 'border-warn/40 bg-sunken text-warn' },
  unverified: { label: 'unverified', cls: 'border-warn/40 bg-sunken text-warn' },
}

/* One past trade, with what a premium seller asks of an old trade: what was
   collected, what came back, how long it was held, how close to expiry it ran,
   and how it ended. A trade bought back at 50% and a trade taken by assignment
   look identical in a P&L column and are not the same trade at all. */
function HistoryRow({
  m,
  onDrop,
  busy,
}: {
  m: NamedMember
  onDrop: () => void
  busy: boolean
}) {
  const pnl = m.is_open ? m.open_pnl : m.realized_pnl
  const value = num(pnl)
  const dteIn = m.dte_at_entry === null ? '—' : String(m.dte_at_entry)
  const dteOut = m.is_open
    ? m.dte_now === null
      ? '—'
      : `${m.dte_now} left`
    : m.dte_at_close === null
      ? '—'
      : String(m.dte_at_close)
  const end = ENDING[m.ending]

  return (
    <tr className="border-t border-line/60 align-top hover:bg-hover">
      <td className="py-1.5 pl-3 pr-2 whitespace-nowrap text-muted">
        {shortDate(m.opened)}
        <span className="text-faint"> → </span>
        {m.is_open ? <span className="text-faint">now</span> : shortDate(m.closed)}
      </td>
      <td className="py-1.5 pr-2 whitespace-nowrap font-medium">{m.underlying}</td>
      <td className="mono py-1.5 pr-2 text-[11px] text-faint">{m.legs.join('  ·  ')}</td>
      <td className="num py-1.5 pr-2 text-right text-muted">
        {m.days_held === null ? '—' : `${m.days_held}d`}
      </td>
      <td className="num py-1.5 pr-2 text-right text-muted whitespace-nowrap">
        {dteIn}
        <span className="text-faint"> → </span>
        {dteOut}
      </td>
      <td className="num py-1.5 pr-2 text-right text-muted">
        {money(m.credit, { sign: true, cents: false })}
      </td>
      <td
        className={`num py-1.5 pr-2 text-right font-medium ${
          value === null ? 'text-faint' : value >= 0 ? 'text-profit' : 'text-loss'
        }`}
      >
        {pnl === null ? '—' : money(pnl, { sign: true, cents: false })}
      </td>
      <td className="num py-1.5 pr-2 text-right text-muted">
        {m.captured === null ? '—' : pct(m.captured, 0)}
      </td>
      <td className="py-1.5 pr-2 whitespace-nowrap">
        <span className={`rounded-sm border px-1.5 py-0.5 text-[10px] ${end.cls}`}>{end.label}</span>
        {m.roll_count > 0 && (
          <span className="ml-1 text-[10px] text-faint">rolled {m.roll_count}×</span>
        )}
      </td>
      <td className="py-1.5 pr-3 text-right">
        <button
          onClick={onDrop}
          disabled={busy}
          title="Remove this trade from the strategy"
          className="rounded-sm px-1.5 text-[11px] text-faint hover:bg-hover hover:text-loss disabled:opacity-40"
        >
          ×
        </button>
      </td>
    </tr>
  )
}

function History({
  members,
  onDrop,
  busy,
}: {
  members: NamedMember[]
  onDrop: (id: string) => void
  busy: string | null
}) {
  const closed = members.filter((m) => !m.is_open)
  const wins = closed.filter((m) => m.outcome === 'win').length
  const losses = closed.filter((m) => m.outcome === 'loss').length
  const total = members.reduce((acc, m) => {
    const v = num(m.is_open ? m.open_pnl : m.realized_pnl)
    return acc + (v ?? 0)
  }, 0)

  if (members.length === 0) {
    return (
      <p className="mt-3 border-t border-line pt-3 text-[11px] text-faint">
        No trades in this strategy yet. Use "Find older trades" below.
      </p>
    )
  }

  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="mb-1.5 flex flex-wrap items-baseline gap-2">
        <h4 className="text-[11px] font-medium uppercase tracking-wider text-faint">
          History — {members.length} trade{members.length === 1 ? '' : 's'}
        </h4>
        <span className="text-[11px] text-faint">
          {wins}W / {losses}L closed
        </span>
        <span
          className={`num ml-auto text-xs font-medium ${total >= 0 ? 'text-profit' : 'text-loss'}`}
        >
          {money(total, { sign: true, cents: false })} all in
        </span>
      </div>

      <div className="overflow-x-auto rounded-sm border border-line">
        <table className="w-full min-w-[820px] text-xs">
          <thead>
            <tr className="border-b border-line text-left text-[10px] uppercase tracking-wider text-faint">
              <th className="py-1.5 pl-3 pr-2 font-medium">Dates</th>
              <th className="py-1.5 pr-2 font-medium">Contract</th>
              <th className="py-1.5 pr-2 font-medium">Legs</th>
              <th className="py-1.5 pr-2 text-right font-medium">Held</th>
              <th className="py-1.5 pr-2 text-right font-medium">DTE in → out</th>
              <th className="py-1.5 pr-2 text-right font-medium">Credit / debit</th>
              <th className="py-1.5 pr-2 text-right font-medium">P&L</th>
              <th className="py-1.5 pr-2 text-right font-medium">Captured</th>
              <th className="py-1.5 pr-2 font-medium">Ended</th>
              <th className="w-8 py-1.5 pr-3" />
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <HistoryRow key={m.id} m={m} busy={busy === m.id} onDrop={() => onDrop(m.id)} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[10px] text-faint">
        Captured is P&L against the credit taken in — blank where max profit cannot be known, such
        as a trade that ended in assignment.
      </p>
    </div>
  )
}

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
      className={`cursor-pointer border-t border-line/60 px-2 py-1.5 text-xs ${
        picked ? 'bg-accent-soft' : 'hover:bg-hover'
      }`}
    >
      <div className="flex items-baseline gap-2">
        <input type="checkbox" checked={picked} readOnly className="pointer-events-none" />
        <span className="w-20 shrink-0 text-muted">{fullDate(match.opened)}</span>
        <span className="w-16 shrink-0 font-medium">{match.underlying}</span>
        <span className="mono flex-1 truncate text-faint">{match.legs.join('; ')}</span>
        <span className="w-12 shrink-0 text-right text-faint">{pct(match.score, 0)}</span>
        <span
          className={`num w-20 shrink-0 text-right ${
            (num(match.realized_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
          }`}
        >
          {money(match.realized_pnl, { sign: true, cents: false })}
        </span>
      </div>
      <div className="num flex flex-wrap gap-x-3 pl-[1.9rem] pt-0.5 text-[10px] text-faint">
        <span>held {match.days_held === null ? '—' : `${match.days_held}d`}</span>
        <span>
          DTE {match.dte_at_entry ?? '—'} → {match.dte_at_close ?? '—'}
        </span>
        <span>credit {money(match.credit, { sign: true, cents: false })}</span>
        <span>captured {match.captured === null ? '—' : pct(match.captured, 0)}</span>
        <span>{ENDING[match.ending].label}</span>
        {match.roll_count > 0 && <span>rolled {match.roll_count}×</span>}
      </div>
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
  const [dropping, setDropping] = useState<string | null>(null)

  const p = strategy.performance

  async function drop(tradeId: string) {
    setDropping(tradeId)
    try {
      await api.dropMember(strategy.id, tradeId)
      onChange()
      if (open) matches.reload()
    } finally {
      setDropping(null)
    }
  }

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

      <History members={strategy.members} onDrop={(id) => void drop(id)} busy={dropping} />

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
