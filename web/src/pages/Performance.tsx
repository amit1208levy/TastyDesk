import { useState } from 'react'
import { StatTile } from '../components/StatTile'
import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { LossShape } from '../components/LossShape'
import { ALL_TIME, PeriodPicker } from '../components/PeriodPicker'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, moneyCompact, pct, decimals, num, EM_DASH } from '../lib/format'
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

function StatsTable({ rows }: { rows: [string, PerformanceStats][] }) {
  // Same rule as the positions table: a column with nothing on any row is left
  // out rather than drawn as a line of dashes. Here it is capture — which only
  // exists for winners of structures with a knowable ceiling — and days held.
  const has = (key: keyof PerformanceStats) => rows.some(([, s]) => s[key] !== null)
  const showCapture = has('avg_pct_of_max_profit_captured')
  const showDays = has('avg_days_in_trade')
  if (rows.length === 0) return <Empty title="No closed trades yet." />
  return (
    <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
      <table className="w-max text-[16px]">
        <thead>
          <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
            <th className="py-3 pl-4 pr-3 font-medium">Group</th>
            <th className="py-3 pr-3 text-right font-medium">Trades</th>
            <th className="py-3 pr-3 text-right font-medium">Win rate</th>
            <th className="py-3 pr-3 text-right font-medium">Avg win</th>
            <th className="py-3 pr-3 text-right font-medium">Avg loss</th>
            <th className="py-3 pr-3 text-right font-medium" title="(win rate x avg win) - (loss rate x avg loss)">
              Expectancy
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
                  <span className="ml-1.5 text-[12px] font-normal text-faint" title="Too few trades to draw a conclusion">
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

  const [period, setPeriod] = useState<Period>(ALL_TIME)
  const overall = useAsync(() => api.performance(period), [period])
  const pace = useAsync(() => api.performancePace(period), [period])
  const sliced = useAsync(
    () =>
      dim === 'named'
        ? api.performanceByNamed(period)
        : dim === 'strategy'
          ? api.performanceByType(period)
          : dim === 'underlying'
            ? api.performanceByUnderlying(period)
            : api.performanceByBucket(dim, period),
    [dim, period],
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
      <PeriodPicker value={period} onChange={setPeriod} />

      <section>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <StatTile label="Closed trades" value={o.trades} sub={`${o.wins}W / ${o.losses}L`} />
          <StatTile
            label="Win rate"
            value={o.win_rate === null ? EM_DASH : pct(o.win_rate, 0)}
            tone="muted"
            sub={o.trades < 20 ? 'thin sample' : period.label.toLowerCase()}
          />
          <StatTile
            label="Expectancy"
            value={money(o.expectancy, { sign: true })}
            tone={(num(o.expectancy) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub="per trade"
            title="(win rate x average win) − (loss rate x average loss)"
          />
          {/* What the pace of trading turns expectancy into: a month and a
              year at the rate trades have actually been closing. */}
          <StatTile
            label="Expected a month"
            value={pace.data?.per_month == null ? EM_DASH : money(pace.data.per_month, { sign: true, cents: false })}
            tone={(num(pace.data?.per_month ?? null) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub={
              pace.data?.trades_per_month == null
                ? undefined
                : `${decimals(pace.data.trades_per_month, 1)} trades a month × expectancy`
            }
          />
          <StatTile
            label="Expected a year"
            value={pace.data?.per_year == null ? EM_DASH : money(pace.data.per_year, { sign: true, cents: false })}
            tone={(num(pace.data?.per_year ?? null) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub="the monthly pace × 12"
          />
          <StatTile
            label="Total P&L"
            value={moneyCompact(o.total_pnl)}
            tone={(num(o.total_pnl) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub="net of fees"
          />
          <StatTile
            label="Profit factor"
            value={o.profit_factor === null ? EM_DASH : decimals(o.profit_factor, 2)}
            tone="muted"
            sub={o.profit_factor === null ? 'no losses yet' : 'gross win / gross loss'}
          />
          <StatTile
            label="Avg days held"
            value={decimals(o.avg_days_in_trade, 0)}
            tone="muted"
            sub={
              o.avg_pct_of_max_profit_captured === null
                ? undefined
                : `${pct(o.avg_pct_of_max_profit_captured, 0)} of max, on winners`
            }
          />
        </div>
      </section>

      <section>
        <SectionHeading title="The numbers" hint="every row reports its sample size" />
        <div className="mb-2.5 flex flex-wrap gap-1">
          {DIMENSIONS.map((d) => (
            <button
              key={d.id}
              onClick={() => setDim(d.id)}
              className={`rounded-sm border px-2 py-0.5 text-[13px] transition-colors ${
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
          <p className="rounded-card border border-line bg-raised p-4 text-[15px] text-muted">
            None of your {o.trades} closed trades has this recorded — it was not in the
            transaction record at entry. Positions opened from now on carry it, and this fills in
            as they close.
          </p>
        ) : (
          <StatsTable rows={rows} />
        )}
      </section>
      <LossShape period={period} />
    </div>
  )
}
