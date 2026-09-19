import { Exposure } from '../components/Exposure'
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
  // Short enough to survive the tile without an ellipsis: a headline figure
  // that has to be hovered to be read is not a headline.
  if (bad > 0) return { label: `${bad} to decide`, tone: 'loss' }
  if (tested > 0) return { label: `${tested} tested`, tone: 'neutral' }
  return { label: 'all quiet', tone: 'muted' }
}

export function Positions() {
  const summary = useAsync(() => api.summary(), [], 30_000)
  const strategies = useAsync(() => api.openStrategies(), [], 30_000)
  const greeks = useAsync(() => api.greeks(), [], 30_000)

  if (summary.error) return <ErrorPanel error={summary.error} onRetry={summary.reload} />
  if (strategies.error) return <ErrorPanel error={strategies.error} onRetry={strategies.reload} />
  if (!summary.data || !strategies.data) return <Loading label="Reading your account" />

  const s = summary.data
  const views = strategies.data
  const g = greeks.data ?? null
  const att = attention(views)

  const atTarget = views.filter((v) => {
    const p = num(v.pnl.pct_of_credit)
    return p !== null && p >= 0.5
  }).length

  const past21 = views.filter((v) => v.risk.dte !== null && v.risk.dte <= 21).length

  return (
    <div className="space-y-5">
      <section>
        <div className="stagger grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
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
            label="Delta (SPY)"
            value={g === null ? '—' : decimals(g.beta_weighted_delta, 1)}
            tone="muted"
            sub={
              g === null
                ? 'beta weighted'
                : `${money(g.dollars_per_spy_percent, { sign: true, cents: false })} per 1% SPY`
            }
            title="Every product's dollar delta scaled by its beta to SPY, then divided by the SPY price. The only way to add a soybean delta to a Best Buy delta."
          />
          <StatTile
            label="Net theta"
            value={money(s.net_theta, { sign: true, cents: false })}
            tone={(num(s.net_theta) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub="dollars per day"
          />
          <StatTile
            label="Net vega"
            value={g === null ? '—' : money(g.vega, { sign: true, cents: false })}
            tone={g === null || (num(g.vega) ?? 0) >= 0 ? 'muted' : 'profit'}
            sub="per 1 point of IV"
            title="What one point of implied volatility is worth to the book. Negative is the normal state for a premium seller."
          />
          <StatTile label="Attention" value={att.label} tone={att.tone} sub={`${past21} inside 21 DTE`} />
        </div>
      </section>

      {(atTarget > 0 || past21 > 0) && (
        <section className="sheened relative overflow-hidden rounded-card border border-line bg-raised px-6 py-5">
          <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-accent/70" />
          <div className="text-[12px] font-medium uppercase tracking-[0.14em] text-accent">
            Your rules say
          </div>
          <ul className="mt-2 space-y-1.5 text-[17px] leading-relaxed">
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

      {g && <Exposure g={g} />}

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
