import { StatTile } from '../components/StatTile'
import { StrategyTable } from '../components/StrategyTable'
import { Loading, ErrorPanel, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, moneyCompact, decimals, pct, num } from '../lib/format'
import type { StrategyView } from '../types'

function attention(views: StrategyView[]): { label: string; tone: 'neutral' | 'loss' | 'muted' } {
  const bad = views.filter((v) => v.risk.level === 'Danger' || v.risk.level === 'Critical').length
  const tested = views.filter((v) => v.risk.level === 'Tested').length
  if (bad > 0) return { label: `${bad} need${bad === 1 ? 's' : ''} a decision`, tone: 'loss' }
  if (tested > 0) return { label: `${tested} tested`, tone: 'neutral' }
  return { label: 'nothing tested', tone: 'muted' }
}

export function Positions() {
  const summary = useAsync(() => api.summary(), [], 30_000)
  const strategies = useAsync(() => api.openStrategies(), [], 30_000)

  if (summary.error) return <ErrorPanel error={summary.error} onRetry={summary.reload} />
  if (strategies.error) return <ErrorPanel error={strategies.error} onRetry={strategies.reload} />
  if (!summary.data || !strategies.data) return <Loading label="Reading your account" />

  const s = summary.data
  const views = strategies.data
  const att = attention(views)

  const atTarget = views.filter((v) => {
    const p = num(v.pnl.pct_of_credit)
    return p !== null && p >= 0.5
  }).length

  const past21 = views.filter((v) => v.risk.dte !== null && v.risk.dte <= 21).length

  return (
    <div className="space-y-5">
      <section>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
          <StatTile label="Net liq" value={moneyCompact(s.net_liquidating_value)} sub={`${s.open_strategies} open`} />
          <StatTile
            label="Open P&L"
            value={money(s.open_pnl, { sign: true, cents: false })}
            tone={(num(s.open_pnl) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub="across all strategies"
          />
          <StatTile
            label="Buying power used"
            value={moneyCompact(s.buying_power_used)}
            sub={
              num(s.net_liquidating_value)
                ? `${pct(num(s.buying_power_used)! / num(s.net_liquidating_value)!, 0)} of net liq`
                : undefined
            }
          />
          <StatTile
            label="Net delta"
            value={decimals(s.net_delta, 0)}
            tone="muted"
            sub="share equivalent"
            title="Sum of position deltas across every leg you hold"
          />
          <StatTile
            label="Net theta"
            value={money(s.net_theta, { sign: true })}
            tone={(num(s.net_theta) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub="per day"
          />
          <StatTile label="Attention" value={att.label} tone={att.tone} sub={`${past21} inside 21 DTE`} />
        </div>
      </section>

      {(atTarget > 0 || past21 > 0) && (
        <section className="rounded-card border border-line bg-raised px-4 py-3">
          <div className="text-[11px] font-medium uppercase tracking-wider text-faint">Your rules say</div>
          <ul className="mt-1.5 space-y-1 text-sm">
            {atTarget > 0 && (
              <li className="text-ink">
                <span className="num font-medium text-profit">{atTarget}</span>{' '}
                {atTarget === 1 ? 'strategy is' : 'strategies are'} at or past 50% of max profit — your
                management target.
              </li>
            )}
            {past21 > 0 && (
              <li className="text-ink">
                <span className="num font-medium text-tested">{past21}</span>{' '}
                {past21 === 1 ? 'strategy is' : 'strategies are'} inside 21 DTE — close or roll.
              </li>
            )}
          </ul>
        </section>
      )}

      <section>
        <SectionHeading
          title="Open strategies"
          hint="ordered by what needs attention — risk is assessed on the whole structure, never on one leg"
        />
        <StrategyTable views={views} />
      </section>
    </div>
  )
}
