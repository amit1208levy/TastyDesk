import { useMemo, useState } from 'react'
import { EquityCurve } from '../components/EquityCurve'
import { ErrorPanel, Loading, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, shortDate, num } from '../lib/format'
import { equityCurve, statsOf } from '../lib/stats'
import type { MatchReport, NamedMember, NamedStrategy, StrategyMatch } from '../types'

const ENDING: Record<NamedMember['ending'], { label: string; cls: string }> = {
  open: { label: 'open', cls: 'border-accent/40 bg-accent-soft text-accent' },
  closed: { label: 'closed', cls: 'border-line bg-sunken text-muted' },
  expired: { label: 'expired', cls: 'border-line bg-sunken text-muted' },
  assigned: { label: 'assigned', cls: 'border-warn/40 bg-sunken text-warn' },
  unverified: { label: 'unverified', cls: 'border-warn/40 bg-sunken text-warn' },
}

type Row = NamedMember & Partial<StrategyMatch> & { yours?: boolean }

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[12px] uppercase tracking-wider text-faint">{label}</div>
      <div className={`num text-[16px] font-medium ${tone ?? ''}`}>{value}</div>
    </div>
  )
}

/* One trade in a strategy's history.

   A row the app matched rather than the user grouped says so, says how sure it
   is, and opens to show what that confidence was built on — the agreements, the
   disagreements, and what could not be checked at all. */
function HistoryRow({
  r,
  onAdopt,
  onDrop,
  busy,
}: {
  r: Row
  onAdopt: () => void
  onDrop: () => void
  busy: boolean
}) {
  const [open, setOpen] = useState(false)
  const pnl = r.is_open ? r.open_pnl : r.realized_pnl
  const value = num(pnl)
  const end = ENDING[r.ending]
  const confidence = r.confidence ?? null

  return (
    <>
      <tr
        className={`cursor-pointer border-t border-line/60 align-top hover:bg-hover ${
          r.yours ? '' : 'bg-sunken/30'
        }`}
        onClick={() => setOpen(!open)}
      >
        <td className="py-3.5 pl-3 pr-2 whitespace-nowrap">
          {r.yours ? (
            <span className="rounded-sm border border-accent/40 bg-accent-soft px-1 py-0.5 text-[11px] uppercase text-accent">
              yours
            </span>
          ) : (
            <span className="num text-[12px] text-muted" title="How sure the app is that this belongs here">
              {confidence === null ? '' : pct(confidence, 0)}
            </span>
          )}
        </td>
        <td className="py-3.5 pr-2 whitespace-nowrap text-muted">
          {shortDate(r.opened)}
          <span className="text-faint"> → </span>
          {r.is_open ? <span className="text-faint">now</span> : shortDate(r.closed)}
        </td>
        <td className="py-3.5 pr-2 whitespace-nowrap font-medium">{r.underlying}</td>
        <td className="mono py-3.5 pr-2 text-[13px] text-faint">{r.legs.join('  ·  ')}</td>
        <td className="num py-3.5 pr-2 text-right text-muted">
          {r.days_held === null ? '—' : `${r.days_held}d`}
        </td>
        <td className="num py-3.5 pr-2 text-right text-muted whitespace-nowrap">
          {r.dte_at_entry ?? '—'}
          <span className="text-faint"> → </span>
          {r.is_open ? `${r.dte_now ?? '—'} left` : (r.dte_at_close ?? '—')}
        </td>
        <td className="num py-3.5 pr-2 text-right text-muted">
          {money(r.credit, { sign: true, cents: false })}
        </td>
        <td
          className={`num py-3.5 pr-2 text-right font-medium ${
            value === null ? 'text-faint' : value >= 0 ? 'text-profit' : 'text-loss'
          }`}
        >
          {pnl === null ? '—' : money(pnl, { sign: true, cents: false })}
        </td>
        <td className="num py-3.5 pr-2 text-right text-muted">
          {r.captured === null ? '—' : pct(r.captured, 0)}
        </td>
        <td className="py-3.5 pr-2 whitespace-nowrap">
          <span className={`rounded-sm border px-1.5 py-0.5 text-[12px] ${end.cls}`}>{end.label}</span>
          {r.roll_count > 0 && <span className="ml-1 text-[12px] text-faint">rolled {r.roll_count}×</span>}
        </td>
        <td className="py-3.5 pr-3 text-right">
          <button
            onClick={(e) => {
              e.stopPropagation()
              if (r.yours) onDrop()
              else onAdopt()
            }}
            disabled={busy}
            title={r.yours ? 'Remove this trade from the strategy' : 'Keep this one whatever the slider says'}
            className="rounded-sm px-1.5 text-[13px] text-faint hover:bg-hover hover:text-ink disabled:opacity-40"
          >
            {r.yours ? '×' : '+'}
          </button>
        </td>
      </tr>

      {open && !r.yours && (
        <tr className="border-t border-line/40 bg-sunken/50">
          <td colSpan={11} className="px-3 py-3 text-[13px]">
            <div className="flex flex-wrap gap-x-5 gap-y-1">
              {(r.reasons ?? []).length > 0 && (
                <span className="text-profit">✓ {(r.reasons ?? []).join(' · ')}</span>
              )}
              {(r.misses ?? []).length > 0 && (
                <span className="text-loss">✗ {(r.misses ?? []).join(' · ')}</span>
              )}
              {(r.unknowns ?? []).length > 0 && (
                <span className="text-warn">? {(r.unknowns ?? []).join(' · ')}</span>
              )}
              {(r.not_applicable ?? []).length > 0 && (
                <span className="text-faint">– {(r.not_applicable ?? []).join(' · ')}</span>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

function StrategyCard({
  strategy,
  report,
  threshold,
  onChange,
}: {
  strategy: NamedStrategy
  report: MatchReport | undefined
  threshold: number
  onChange: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  // The trade list is the archive, not the answer. The card leads with how the
  // strategy is doing and opens the rows only when asked.
  const [showTrades, setShowTrades] = useState(false)

  const rows = useMemo<Row[]>(() => {
    const mine: Row[] = strategy.members.map((m) => ({ ...m, yours: true }))
    const matched: Row[] = (report?.candidates ?? [])
      .filter((c) => c.confidence >= threshold)
      .map((c) => ({ ...c, yours: false }))
    return [...mine, ...matched].sort((a, b) => (a.opened < b.opened ? 1 : -1))
  }, [strategy.members, report, threshold])

  const stats = useMemo(() => statsOf(rows), [rows])
  const curve = useMemo(() => equityCurve(rows), [rows])

  const nearMisses = useMemo(
    () =>
      (report?.candidates ?? [])
        .filter((c) => c.confidence < threshold && c.confidence >= threshold - 0.2)
        .slice(0, 12),
    [report, threshold],
  )

  async function adopt(tradeId: string) {
    setBusy(tradeId)
    try {
      await api.adoptMatches(strategy.id, [tradeId])
      onChange()
    } finally {
      setBusy(null)
    }
  }

  async function drop(tradeId: string) {
    setBusy(tradeId)
    try {
      await api.dropMember(strategy.id, tradeId)
      onChange()
    } finally {
      setBusy(null)
    }
  }

  const matchedCount = rows.length - strategy.members.length

  return (
    <div className="rounded-card border border-line bg-raised p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-[16px] font-semibold">{strategy.name}</h3>
        <span className="rounded-full border border-line px-1.5 py-0.5 text-[12px] text-muted">
          {strategy.product}
        </span>
        <span className="text-[14px] text-faint">{strategy.shape}</span>
        <span className="ml-auto text-[13px] text-muted">
          {strategy.members.length} yours
          {matchedCount > 0 && (
            <span className="text-faint">
              {' '}
              + {matchedCount} matched at {pct(threshold, 0)}+
            </span>
          )}
        </span>
      </div>

      {strategy.name_reading && (
        <p className="mt-1 text-[13px] text-faint">
          Your name reads like {strategy.name_reading} — shown for your benefit; matching uses the
          trades, not the name.
        </p>
      )}

      <div className="mt-3 grid grid-cols-3 gap-3 border-t border-line pt-3 sm:grid-cols-7">
        <Stat label="Trades" value={String(stats.trades)} />
        <Stat label="Win rate" value={stats.winRate === null ? '—' : pct(stats.winRate, 0)} />
        <Stat
          label="Total P&L"
          value={money(stats.total, { sign: true, cents: false })}
          tone={stats.total >= 0 ? 'text-profit' : 'text-loss'}
        />
        <Stat
          label="Expectancy"
          value={money(stats.expectancy, { sign: true, cents: false })}
          tone={stats.expectancy >= 0 ? 'text-profit' : 'text-loss'}
        />
        <Stat label="Avg win" value={money(stats.avgWin, { cents: false })} />
        <Stat label="Avg loss" value={money(stats.avgLoss, { cents: false })} />
        <Stat label="Captured" value={stats.capture === null ? '—' : pct(stats.capture, 0)} />
      </div>

      <div className="mt-3 border-t border-line pt-2">
        <EquityCurve points={curve} />
      </div>

      <button
        onClick={() => setShowTrades(!showTrades)}
        className="mt-2 text-[13px] text-muted hover:text-ink"
      >
        {showTrades ? 'Hide the trades' : `Show the ${rows.length} trades behind this`}
      </button>

      {showTrades && (
      <div className="mt-2 overflow-x-auto rounded-sm border border-line">
        <table className="w-full min-w-[900px] text-[14px]">
          <thead>
            <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
              <th className="py-3.5 pl-3 pr-2 font-medium">Sure</th>
              <th className="py-3.5 pr-2 font-medium">Dates</th>
              <th className="py-3.5 pr-2 font-medium">Contract</th>
              <th className="py-3.5 pr-2 font-medium">Legs</th>
              <th className="py-3.5 pr-2 text-right font-medium">Held</th>
              <th className="py-3.5 pr-2 text-right font-medium">DTE in → out</th>
              <th className="py-3.5 pr-2 text-right font-medium">Credit / debit</th>
              <th className="py-3.5 pr-2 text-right font-medium">P&L</th>
              <th className="py-3.5 pr-2 text-right font-medium">Captured</th>
              <th className="py-3.5 pr-2 font-medium">Ended</th>
              <th className="w-8 py-3.5 pr-3" />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={11} className="px-3 py-3 text-[13px] text-faint">
                  Nothing in this strategy at {pct(threshold, 0)} confidence.
                </td>
              </tr>
            ) : (
              (showAll ? rows : rows.slice(0, 15)).map((r) => (
                <HistoryRow
                  key={r.id}
                  r={r}
                  busy={busy === r.id}
                  onAdopt={() => void adopt(r.id)}
                  onDrop={() => void drop(r.id)}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
      )}

      {showTrades && rows.length > 15 && (
        <button
          onClick={() => setShowAll(!showAll)}
          className="mt-1.5 text-[13px] text-muted hover:text-ink"
        >
          {showAll ? 'Show fewer' : `Show all ${rows.length}`}
        </button>
      )}

      {nearMisses.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[13px] text-faint">
            {nearMisses.length} just below the bar — not counted
          </summary>
          <ul className="mt-1 space-y-0.5">
            {nearMisses.map((c) => (
              <li key={c.trade_id} className="flex items-baseline gap-2 text-[13px]">
                <span className="num w-9 shrink-0 text-right text-muted">{pct(c.confidence, 0)}</span>
                <span className="w-20 shrink-0 text-faint">{shortDate(c.opened)}</span>
                <span className="w-16 shrink-0 text-faint">{c.underlying}</span>
                <span className="flex-1 truncate text-faint">
                  {[...c.misses, ...c.unknowns].join(' · ')}
                </span>
                <button
                  onClick={() => void adopt(c.trade_id)}
                  disabled={busy === c.trade_id}
                  className="shrink-0 rounded-sm px-1.5 text-[13px] text-muted hover:bg-hover hover:text-ink disabled:opacity-40"
                >
                  add anyway
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

/* The confidence bar, as a setting rather than a control on the page.

   It decides how the user's journal is read, so it belongs with his other
   preferences: set once, saved, and out of the way. What stays visible is one
   line saying what the bar is and how many trades it is letting in, which is
   the part he needs while reading the page. */
function ThresholdSetting({
  value,
  onSave,
  added,
  strategies,
}: {
  value: number
  onSave: (v: number) => Promise<void>
  added: number
  strategies: number
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function save() {
    setSaving(true)
    setProblem(null)
    try {
      await onSave(draft)
      setOpen(false)
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-card border border-line bg-raised px-4 py-2">
      <div className="flex flex-wrap items-baseline gap-2 text-[13px]">
        <span className="text-muted">
          Counting trades the app is at least{' '}
          <span className="num font-medium text-ink">{pct(value, 0)}</span> sure about —{' '}
          <span className="num text-ink">{added}</span> matched across {strategies}{' '}
          {strategies === 1 ? 'strategy' : 'strategies'}.
        </span>
        <button
          onClick={() => {
            setDraft(value)
            setOpen(!open)
          }}
          className="ml-auto rounded-sm border border-line px-2 py-0.5 text-muted hover:bg-hover hover:text-ink"
        >
          {open ? 'Close' : 'Change'}
        </button>
      </div>

      {open && (
        <div className="mt-2 space-y-2 border-t border-line pt-2">
          <p className="text-[13px] text-muted">
            An old trade joins a strategy when the app is at least this sure it belongs. Lower it to
            see what it is refusing to count; the reasons are on every row.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <span className="num w-14 text-[21px] font-semibold">{pct(draft, 0)}</span>
            <input
              type="range"
              min={50}
              max={100}
              step={1}
              value={Math.round(draft * 100)}
              onChange={(e) => setDraft(Number(e.target.value) / 100)}
              className="h-1 min-w-[12rem] flex-1 cursor-pointer appearance-none rounded-full bg-sunken accent-accent"
              aria-label="Minimum confidence for a trade to count"
            />
            <button
              onClick={() => void save()}
              disabled={saving || draft === value}
              className="rounded-sm border border-accent/50 bg-accent-soft px-3 py-1 text-[13px] text-accent disabled:opacity-40"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
          {problem && <div className="text-[13px] text-loss">{problem}</div>}
        </div>
      )}
    </div>
  )
}

export function Strategies() {
  const named = useAsync(() => api.namedStrategies(), [])
  const matches = useAsync(() => api.allMatches(), [])
  const settings = useAsync(() => api.settings(), [])

  const threshold = settings.data?.match_threshold ?? 0.97
  const data = named.data
  const reports = matches.data

  const added = useMemo(() => {
    if (!reports) return 0
    return Object.values(reports).reduce(
      (acc, r) => acc + r.candidates.filter((c) => c.confidence >= threshold).length,
      0,
    )
  }, [reports, threshold])

  if (named.error) return <ErrorPanel error={named.error} onRetry={named.reload} />
  if (!data) return <Loading label="Reading your strategies" />

  function reload() {
    named.reload()
    matches.reload()
  }

  async function saveThreshold(value: number) {
    await api.setSetting('match_threshold', value)
    settings.reload()
  }

  return (
    <div className="space-y-3">
      <SectionHeading
        title="Your strategies"
        hint="what you grouped, plus every older trade the app is sure enough about"
      />

      {data.length === 0 ? (
        <Empty
          title="You have not named any strategies yet."
          hint="Go to Legs, pick the legs that belong to one idea, and name it. The app will then look back through your history for trades like it and say how sure it is about each one."
        />
      ) : (
        <>
          <ThresholdSetting
            value={threshold}
            onSave={saveThreshold}
            added={added}
            strategies={data.length}
          />
          {matches.loading && !reports && (
            <p className="text-[13px] text-faint">Looking back through your history…</p>
          )}
          {data.map((s) => (
            <StrategyCard
              key={s.id}
              strategy={s}
              report={reports?.[s.id]}
              threshold={threshold}
              onChange={reload}
            />
          ))}
        </>
      )}
    </div>
  )
}
