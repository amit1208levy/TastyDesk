import { useState } from 'react'
import { DivergingBars, toBars } from '../components/DivergingBars'
import { StatTile } from '../components/StatTile'
import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { LossShape } from '../components/LossShape'
import { ALL_TIME, PeriodPicker } from '../components/PeriodPicker'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, moneyCompact, pct, decimals, num, EM_DASH } from '../lib/format'
import type { PerformanceStats, Period } from '../types'

const METRICS = [
  { id: 'total_pnl', label: 'Total P&L', hint: 'realized, net of fees' },
  { id: 'expectancy', label: 'Expectancy', hint: 'average outcome per trade' },
  { id: 'pnl_per_bp_day', label: 'P&L per BP-day', hint: 'return on the capital it tied up, per day' },
] as const

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
type Metric = (typeof METRICS)[number]['id']

function StatsTable({ rows }: { rows: [string, PerformanceStats][] }) {
  if (rows.length === 0) return <Empty title="No closed trades yet." />
  return (
    <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
      <table className="w-full min-w-[760px] text-[16px]">
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
            <th className="py-3 pr-3 text-right font-medium">Days held</th>
            <th className="py-3 pr-3 text-right font-medium" title="Of the winners, how much of the available profit was taken. Losers are excluded — a loss is not a capture.">
              % captured (wins)
            </th>
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
              <td className="py-3 pr-3 text-right text-muted">{decimals(s.avg_days_in_trade, 0)}</td>
              <td className="py-3 pr-3 text-right text-muted">
                {s.avg_pct_of_max_profit_captured === null ? EM_DASH : pct(s.avg_pct_of_max_profit_captured, 0)}
              </td>
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
  const [metric, setMetric] = useState<Metric>('expectancy')

  const [period, setPeriod] = useState<Period>(ALL_TIME)
  const overall = useAsync(() => api.performance(period), [period])
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
  const bars = sliced.data ? toBars(sliced.data as never, metric) : []
  const metricMeta = METRICS.find((m) => m.id === metric)!

  return (
    <div className="space-y-5">
      <PeriodPicker value={period} onChange={setPeriod} />

      <section>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
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
        <SectionHeading
          title="What is actually working"
          hint={metricMeta.hint}
          right={
            <div className="flex gap-1">
              {METRICS.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setMetric(m.id)}
                  className={`rounded-sm px-2 py-0.5 text-[13px] transition-colors ${
                    metric === m.id ? 'bg-sunken font-medium text-ink' : 'text-muted hover:bg-hover'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          }
        />

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

        <div className="sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)] p-4">
          {sliced.error ? (
            <ErrorPanel error={sliced.error} onRetry={sliced.reload} />
          ) : !sliced.data ? (
            <Loading />
          ) : nothingRecorded ? (
            <div className="space-y-1.5 py-3 text-[16px]">
              <p className="text-ink">
                None of your {o.trades} closed trades has this recorded.
              </p>
              <p className="text-[14px] text-muted">
                {dim === 'iv_rank_at_entry'
                  ? 'IV rank on the day a trade was opened is not in the transaction record, so it cannot be recovered for a trade from last year. Filling it in with today\u2019s figure would file this week\u2019s volatility as the reason for an old trade, so the app captures it only for positions opened in the last few days.'
                  : 'The delta of the short strike at entry is not in the transaction record either, and back-filling it from today\u2019s prices would be a guess dressed as data.'}{' '}
                It fills in from here: positions opened from now on carry it, and this chart starts
                working as they close.
              </p>
            </div>
          ) : (
            <DivergingBars
              data={bars}
              format={
                metric === 'pnl_per_bp_day'
                  ? (v) => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(3)}`
                  : undefined
              }
              emptyLabel="No closed trades carry this measurement yet."
            />
          )}
        </div>
      </section>

      {!nothingRecorded && (
        <section>
          <SectionHeading title="The numbers" hint="every row reports its sample size" />
          <StatsTable rows={rows} />
        </section>
      )}
      <LossShape period={period} />
    </div>
  )
}
