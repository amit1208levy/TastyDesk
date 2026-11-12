import { GivenBackCard } from '../components/GivenBack'
import { AccountRecord } from '../components/AccountRecord'
import { Scorecard } from '../components/Scorecard'
import { AccountBridge } from '../components/AccountBridge'
import { useEffect, useState } from 'react'
import { OpenNow } from '../components/OpenNow'
import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { LossShape } from '../components/LossShape'
import { StrategyDeepDive } from '../components/StrategyDeepDive'
import { ALL_TIME, PeriodPicker } from '../components/PeriodPicker'
import { api, setIncludeOpen } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, decimals, num, EM_DASH } from '../lib/format'
import { GroupBar, useChosenGroup, useGroups } from '../components/Groups'
import type { PerformanceStats, Period } from '../types'

const DIMENSIONS = [
  { id: 'named', label: 'My strategies' },
  { id: 'strategy', label: 'Structure' },
  { id: 'underlying', label: 'Product' },
  { id: 'dte_at_entry', label: 'DTE at entry' },
  { id: 'iv_rank_at_entry', label: 'IV rank at entry' },
  { id: 'short_delta_at_entry', label: 'Delta at entry' },
] as const

const UNRECORDED = 'not recorded at entry'

type Dim = (typeof DIMENSIONS)[number]['id']

function StatsTable({ rows, netLiq }: { rows: [string, PerformanceStats][]; netLiq: number | null }) {
  // Same rule as the positions table: a column with nothing on any row is left
  // out rather than drawn as a line of dashes. Here it is capture — which only
  // exists for winners of structures with a knowable ceiling — and days held.
  const has = (key: keyof PerformanceStats) => rows.some(([, s]) => s[key] !== null)
  const showCapture = has('avg_pct_of_max_profit_captured')
  const showDays = has('avg_days_in_trade')
  if (rows.length === 0) return <Empty title="No closed trades yet." />
  return (
    <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
      <table className="w-full text-copy">
        <thead>
          <tr className="border-b border-line text-left text-caption uppercase tracking-wider text-faint">
            <th className="w-full py-3 pl-4 pr-3 font-medium">Group</th>
            <th className="py-3 pr-3 text-right font-medium">Trades</th>
            <th className="py-3 pr-3 text-right font-medium">Win rate</th>
            <th className="py-3 pr-3 text-right font-medium">Avg win</th>
            <th className="py-3 pr-3 text-right font-medium">Avg loss</th>
            <th className="py-3 pr-3 text-right font-medium" title="(win rate x avg win) - (loss rate x avg loss)">
              Expectancy
            </th>
            <th
              className="py-3 pr-3 text-right font-medium"
              title="What it makes a month, as a share of today's net liq: its P&L over the months from its first open to its last close"
            >
              Net liq / month
            </th>
            <th
              className="py-3 pr-3 text-right font-medium"
              title="P&L over the buying power it tied up, for as long as it was tied up, a month. Buying power is logged daily from 26 Oct 2026, so this fills in as those trades close."
            >
              BP / month
            </th>
            {showDays && <th className="py-3 pr-3 text-right font-medium">Days held</th>}
            {showCapture && (
              <th className="py-3 pr-3 text-right font-medium" title="Of the winners, how much of the available profit was taken. Losers are excluded — a loss is not a capture.">
                % captured (wins)
              </th>
            )}
            <th className="py-3 pr-4 text-right font-medium">Total P&amp;L</th>
          </tr>
        </thead>
        <tbody className="num">
          {rows.map(([label, s]) => (
            <tr key={label} className="border-b border-line/60 last:border-0 hover:bg-hover">
              <td className="py-3 pl-4 pr-3 font-medium">
                {label}
                {s.trades < 5 && (
                  <span className="ml-1.5 text-caption font-normal text-faint" title="Too few trades to draw a conclusion">
                    thin
                  </span>
                )}
              </td>
              <td className="py-3 pr-3 text-right text-muted">{s.trades}</td>
              <td className="py-3 pr-3 text-right">{s.win_rate === null ? EM_DASH : pct(s.win_rate, 0)}</td>
              <td className="py-3 pr-3 text-right text-profit">{money(s.avg_win, { cents: false })}</td>
              <td className="py-3 pr-3 text-right text-loss">{money(s.avg_loss, { cents: false })}</td>
              <td
                className={`py-3 pr-3 text-right font-medium ${
                  (num(s.expectancy) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
                }`}
              >
                {money(s.expectancy, { sign: true })}
              </td>
              <td
                className={`py-3 pr-3 text-right ${
                  s.pnl_per_month === null || !netLiq ? 'text-faint' : (num(s.pnl_per_month) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
                }`}
              >
                {s.pnl_per_month === null || !netLiq ? EM_DASH : pct((num(s.pnl_per_month) ?? 0) / netLiq, 2)}
              </td>
              <td
                className={`py-3 pr-3 text-right ${
                  s.bp_return_month === null ? 'text-faint' : (num(s.bp_return_month) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
                }`}
              >
                {s.bp_return_month === null ? EM_DASH : pct(s.bp_return_month, 1)}
              </td>
              {showDays && (
                <td className="py-3 pr-3 text-right text-muted">
                  {decimals(s.avg_days_in_trade, 0)}
                </td>
              )}
              {showCapture && (
                <td className="py-3 pr-3 text-right text-muted">
                  {s.avg_pct_of_max_profit_captured === null
                    ? EM_DASH
                    : pct(s.avg_pct_of_max_profit_captured, 0)}
                </td>
              )}
              <td
                className={`py-3 pr-4 text-right font-medium ${
                  (num(s.total_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
                }`}
              >
                {money(s.total_pnl, { sign: true, cents: false })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Performance() {
  const [dim, setDim] = useState<Dim>('named')
  // Spec, or any group of strategies, read as one book; null is everything.
  const groups = useGroups()
  const named = useAsync(() => api.namedStrategies(), [])
  const [groupId, setGroupId] = useChosenGroup('performance')
  const group = groups.groups.find((g) => g.id === groupId) ?? null

  const [period, setPeriod] = useState<Period>(ALL_TIME)
  // Include now: every open trade counted as if closed at today's marks.
  // DPMCCs always count their open LEAP, pressed or not.
  const [now, setNow] = useState(false)
  setIncludeOpen(now)
  // Off again when the page goes, so no other report inherits it.
  useEffect(() => () => setIncludeOpen(false), [])
  const overall = useAsync(() => api.performance(period), [period, now])
  const pace = useAsync(() => api.performancePace(period), [period, now])
  const summary = useAsync(() => api.summary(), [])
  const sliced = useAsync(
    () =>
      dim === 'named'
        ? api.performanceByNamed(period)
        : dim === 'strategy'
          ? api.performanceByType(period)
          : dim === 'underlying'
            ? api.performanceByUnderlying(period)
            : api.performanceByBucket(dim, period),
    [dim, period, now],
  )

  if (overall.error) return <ErrorPanel error={overall.error} onRetry={overall.reload} />
  if (!overall.data) return <Loading label="Reading your trade history" />

  const o = overall.data
  const rows = sliced.data ? Object.entries(sliced.data).filter(([, s]) => s.trades > 0) : []
  // A dimension where every trade lands in "not recorded at entry" has nothing
  // to say. Drawing one full-width bar labelled that way looks like a broken
  // chart; it is actually a gap in the record, and the page should say so.
  const nothingRecorded = rows.length === 1 && rows[0][0] === UNRECORDED

  return (
    <div className="space-y-5">
      <AccountRecord period={period} />
      <div className="flex flex-wrap items-center gap-3 pt-4">
        <div className="min-w-0 flex-1">
          <PeriodPicker value={period} onChange={setPeriod} />
        </div>
        <button
          onClick={() => setNow(!now)}
          title="Count every open trade as if you closed it at today's prices"
          className={`rounded-card border px-4 py-2 text-copy font-medium transition-colors ${
            now ? 'border-accent bg-accent text-white' : 'border-line bg-raised text-ink hover:bg-hover'
          }`}
        >
          {now ? 'Including now ✓' : 'Include now'}
        </button>
      </div>
      <p className="-mt-2 text-note text-muted">
        {now
          ? 'Every open trade is counted as if closed at today’s prices.'
          : 'Closed trades only — except DPMCCs, which always count their open LEAP at today’s price.'}
      </p>
      <GivenBackCard period={period} />

      <GroupBar
        groups={groups.groups}
        value={group?.id ?? null}
        onChange={setGroupId}
        onSave={groups.save}
        allLabel="Everything"
        strategies={(named.data ?? []).map((n) => ({ id: n.id, label: `${n.name} (${n.product})` }))}
      />

      {group ? (
        <GroupView
          key={`${group.id}-${now}-${group.members.join(",")}`}
          id={group.id}
          name={group.name}
          period={period}
          now={now}
          netLiq={num(summary.data?.net_liquidating_value ?? null)}
        />
      ) : (
      <>
      <OpenNow />

      <Scorecard o={o} pace={pace.data ?? null} now={now} period={period} netLiq={num(summary.data?.net_liquidating_value ?? null)} />

      <AccountBridge period={period} now={now} />

      <StrategyDeepDive key={`deep-${now}`} period={period} />

      <section>
        <SectionHeading title="The numbers" hint="every row reports its sample size" />
        <div className="mb-2.5 flex flex-wrap gap-1">
          {DIMENSIONS.map((d) => (
            <button
              key={d.id}
              onClick={() => setDim(d.id)}
              className={`rounded-sm border px-2 py-0.5 text-note transition-colors ${
                dim === d.id
                  ? 'border-line-strong bg-sunken font-medium text-ink'
                  : 'border-line text-muted hover:bg-hover'
              }`}
            >
              {d.label}
            </button>
          ))}
        </div>
        {sliced.error ? (
          <ErrorPanel error={sliced.error} onRetry={sliced.reload} />
        ) : !sliced.data ? (
          <Loading />
        ) : nothingRecorded ? (
          <p className="rounded-card border border-line bg-raised p-4 text-copy text-muted">
            None of your {o.trades} closed trades has this recorded — it was not in the
            transaction record at entry. Positions opened from now on carry it, and this fills in
            as they close.
          </p>
        ) : (
          <StatsTable rows={rows} netLiq={num(summary.data?.net_liquidating_value ?? null)} />
        )}
      </section>
      <LossShape key={`loss-${now}`} period={period} />
      </>
      )}
    </div>
  )
}

function GroupView({
  id,
  name,
  period,
  now,
  netLiq,
}: {
  id: string
  name: string
  period: Period
  now: boolean
  netLiq: number | null
}) {
  const report = useAsync(() => api.groupPerformance(id, period), [id, period, now])
  if (report.error) return <ErrorPanel error={report.error} onRetry={report.reload} />
  if (!report.data) return <Loading label={`Reading ${name}`} />
  const r = report.data
  if (r.group.members.length === 0)
    return (
      <Empty
        title={`Nothing in ${name} yet.`}
        hint={`Press "Choose what's in ${name}" above and tick the strategies that belong in it.`}
      />
    )
  const rows = Object.entries(r.by_strategy)
    .filter(([, st]) => st.trades > 0)
    .sort((a, b) => b[1].trades - a[1].trades)
  return (
    <>
      <Scorecard o={r.overall} pace={r.pace} now={now} period={period} netLiq={netLiq} />
      <section>
        <SectionHeading title={`Each strategy in ${name}`} hint="every row reports its sample size" />
        <StatsTable rows={rows} netLiq={netLiq} />
      </section>
    </>
  )
}
