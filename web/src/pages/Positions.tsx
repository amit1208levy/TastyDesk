import { Decisions } from '../components/Decisions'
import { Exposure } from '../components/Exposure'
import { FieldPicker } from '../components/FieldPicker'
import { StatTile } from '../components/StatTile'
import { StrategyTable } from '../components/StrategyTable'
import { Loading, ErrorPanel, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { useRef, useState } from 'react'
import { money, moneyCompact, decimals, pct, num, relativeTime } from '../lib/format'
import type { FieldSpec } from '../lib/fields'
import type { StrategyView } from '../types'

/** The chosen fields, in the chosen order. Falls back to the defaults. */
function pick(catalogue: FieldSpec[] | undefined, chosen: string[] | undefined): FieldSpec[] {
  if (!catalogue) return []
  const byId = new Map(catalogue.map((f) => [f.id, f]))
  const ids = chosen?.length ? chosen : catalogue.filter((f) => f.default).map((f) => f.id)
  return ids.map((id) => byId.get(id)).filter((f): f is FieldSpec => f !== undefined)
}

/** "since Friday", "since yesterday" — the day the baseline marks are from. */
function sinceLabel(iso: string | null, bare = false): string {
  if (!iso) return bare ? 'the last session' : ''
  const then = new Date(`${iso}T00:00:00`)
  const days = Math.round((Date.now() - then.getTime()) / 86_400_000)
  // Short forms: the line under a tile is one line, and "since Saturday"
  // truncated to "since Sa…" says less than "since Sat".
  const label =
    days <= 1
      ? 'yesterday'
      : days < 7
        ? then.toLocaleDateString(undefined, { weekday: 'short' })
        : then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  return bare ? label : ` · since ${label}`
}

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
  const settings = useAsync(() => api.settings(), [])
  const fields = useAsync(() => api.fields(), [])
  const [customising, setCustomising] = useState(false)
  // What the decision list and the exposure table point at.
  const [focus, setFocus] = useState<string | null>(null)
  const tableRef = useRef<HTMLDivElement>(null)

  function focusOn(idOrProduct: string) {
    setFocus(idOrProduct)
    tableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

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

  // The day's move. It is blank rather than zero until a session has been
  // recorded to measure against: nothing has "not moved" before there is a
  // mark to compare with.
  const day = num(s.day_change)
  const dayValue = day === null ? '—' : money(s.day_change, { sign: true, cents: false })
  const dayTone: 'profit' | 'loss' | 'muted' = day === null ? 'muted' : day >= 0 ? 'profit' : 'loss'
  const daySub =
    day === null
      ? 'no earlier session recorded yet'
      : s.day_change_of < s.open_strategies
        ? `${s.day_change_of} of ${s.open_strategies}${sinceLabel(s.day_change_since)}`
        : `since ${sinceLabel(s.day_change_since, true)}`

  return (
    <div className="space-y-9">
      <section>
        {/* Four across, not seven. Seven tiles in the width of the page left
            each one about 170px, which turned "Buying power" into "Buying …"
            and $45,231 into "$45…": a headline figure that has to be hovered
            to be read is not a headline. */}
        <div className="stagger grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <StatTile label="Net liq" value={moneyCompact(s.net_liquidating_value)} sub={`${s.open_strategies} open`} />
          <StatTile
            label="Open P&L"
            value={money(s.open_pnl, { sign: true, cents: false })}
            tone={(num(s.open_pnl) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub={s.as_of ? `priced ${relativeTime(s.as_of)}` : 'across all strategies'}
          />
          <StatTile
            label="P&L today"
            value={dayValue}
            tone={dayTone}
            sub={daySub}
            title="What the open book has done since the marks it carried before today. Positions with no mark from before today — anything opened since — are left out rather than counted as flat, and the line underneath says how many are in the figure."
          />
          <StatTile
            label="Buying power"
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

      <Decisions views={views} onPick={focusOn} />

      {false && (
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

      {g && <Exposure g={g} onPickProduct={focusOn} />}

      <section>
        <SectionHeading
          title="Open strategies"
          help={
            'Every position you have open, sorted by what needs a hand first rather than by size ' +
            'or by name: a trade through its stop comes before a winner at its target, and "leave ' +
            'it" sorts last. Risk here is always read on the whole structure and never on one leg ' +
            '— a short put down 300% inside a spread whose long put gained at the same time is not ' +
            'a 300% problem. Hover any heading to see what that column measures, click it to sort, ' +
            'and use Customise to choose which columns appear and in what order.'
          }
          right={
            <button
              onClick={() => setCustomising(!customising)}
              className="rounded-sm border border-line px-3 py-1 text-[13px] text-muted hover:bg-hover hover:text-ink"
            >
              {customising ? 'Done' : 'Customise'}
            </button>
          }
        />

        {customising && fields.data && settings.data && (
          <div className="mb-4 space-y-3">
            <FieldPicker
              title="Columns on this table"
              catalogue={fields.data.strategy}
              chosen={settings.data.position_columns}
              onChange={async (ids) => {
                await api.setSetting('position_columns', JSON.stringify(ids))
                settings.reload()
              }}
              onClose={() => setCustomising(false)}
            />
            <FieldPicker
              title="The leg template"
              catalogue={fields.data.leg}
              chosen={settings.data.leg_columns}
              onChange={async (ids) => {
                await api.setSetting('leg_columns', JSON.stringify(ids))
                settings.reload()
              }}
              onClose={() => setCustomising(false)}
            />
          </div>
        )}

        <div ref={tableRef} />
        <StrategyTable
          views={views}
          focus={focus}
          onClearFocus={() => setFocus(null)}
          columns={pick(fields.data?.strategy, settings.data?.position_columns)}
          catalogue={fields.data?.strategy ?? []}
          legColumns={pick(fields.data?.leg, settings.data?.leg_columns)}
          legCatalogue={fields.data?.leg ?? []}
        />
      </section>
    </div>
  )
}
