import { useState } from 'react'
import { Decisions } from '../components/Decisions'
import { StatTile } from '../components/StatTile'
import { Empty, ErrorPanel, Loading, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, moneyCompact, num, pct, relativeTime } from '../lib/format'
import type { ScanCandidate, StrategyView } from '../types'
import { Allocation, GaugeCard, REGIME_TONE, card } from './Tom'

/* The home page: how the account is doing and what to do about it, in the
   order a trading day asks. Every figure here is the same one its own page
   shows — this page reads the same endpoints and adds nothing of its own. */

const FIX_LABEL: Record<string, string> = {
  history: 'Habit',
  book: 'The book',
  plan: 'Strategy mix',
}

function monthStart(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`
}

/* One sentence: the day, what needs him, and the market. */
function headline(dayChange: number | null, decide: number, regime: string | null): string {
  const parts: string[] = []
  if (dayChange !== null) parts.push(`${dayChange >= 0 ? 'Up' : 'Down'} ${money(Math.abs(dayChange), { cents: false })} today.`)
  parts.push(
    decide === 0 ? 'Nothing needs a decision.' : `${decide} ${decide === 1 ? 'position needs' : 'positions need'} a decision.`,
  )
  if (regime) parts.push(`The market is ${regime.toLowerCase()}.`)
  return parts.join(' ')
}

function needsHand(views: StrategyView[], ignored: Record<string, string>): number {
  return views.filter(
    (v) =>
      v.verdict &&
      v.verdict.tone !== 'none' &&
      v.verdict.tone !== 'watch' &&
      ignored[v.strategy.id] !== v.verdict.action,
  ).length
}

function Idea({ c }: { c: ScanCandidate }) {
  const credit = c.plan ? num(c.plan.credit) : null
  return (
    <li className={`${card} lift flex flex-col px-5 py-4`}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="display text-[20px]">{c.symbol}</span>
        <span className="text-[13px] text-muted">{c.setup_name}</span>
      </div>
      <p className="mt-2 flex-1 text-[14px] leading-relaxed text-muted">{c.headline}</p>
      {c.plan && (
        <div className="mt-3 text-[13px] text-faint">
          {c.plan.lots} lot{c.plan.lots === 1 ? '' : 's'}
          {credit !== null && ` · ${money(credit, { cents: false })} ${credit >= 0 ? 'credit' : 'debit'}`}
          {c.plan.bp_per_lot && ` · ${moneyCompact(num(c.plan.bp_per_lot)! * c.plan.lots)} BP`}
        </div>
      )}
    </li>
  )
}

export function Today({ onOpen, onTab }: { onOpen: (id: string) => void; onTab: (tab: 'plan' | 'scanner') => void }) {
  const summary = useAsync(() => api.summary(), [], 30_000)
  const views = useAsync(() => api.openStrategies(), [], 30_000)
  const tom = useAsync(() => api.tom(), [], 120_000)
  // Summed from the closed trades themselves: a report from the 1st would
  // also count the whole past of a strategy carried into New Levy.
  const closed = useAsync(() => api.closedStrategies(1000), [])
  const settings = useAsync(() => api.settings(), [])
  // The scanner reads every chart it knows and takes a while: it fills in last.
  const scan = useAsync(() => api.scanner(), [])
  const [ignored, setIgnored] = useState<Record<string, string> | null>(null)

  if (summary.error) return <ErrorPanel error={summary.error} onRetry={summary.reload} />
  if (views.error) return <ErrorPanel error={views.error} onRetry={views.reload} />
  if (!summary.data || !views.data) return <Loading label="Reading your account" />

  const s = summary.data
  const netLiq = num(s.net_liquidating_value)
  const day = num(s.day_change) ?? num(s.day_change_open)
  const open = num(s.open_pnl)
  const thisMonth = (closed.data ?? []).filter((v) => (v.strategy.closed_at ?? '') >= monthStart())
  const banked = closed.data
    ? thisMonth.reduce((acc, v) => acc + (num(v.strategy.realized_pnl) ?? 0), 0)
    : null
  const monthShare = banked !== null && netLiq ? banked / netLiq : null
  const hidden = ignored ?? settings.data?.ignored_decisions ?? {}
  const market = tom.data?.market ?? null

  const ideas = (scan.data?.candidates ?? [])
    .filter((c) => c.status === 'ready' && c.fit?.bp_ok !== false && c.fit?.strategy_ok !== false)
    .slice(0, 3)

  return (
    <div className="space-y-10">
      <section className="rise">
        <div className="label">
          {new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}
        </div>
        <h1 className="display mt-2 text-[28px] leading-snug sm:text-[32px]">
          {headline(day, needsHand(views.data, hidden), market?.label ?? null)}
        </h1>
        {market && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[14px] text-muted">
            <span
              className={`rounded-full px-2.5 py-0.5 text-[12px] font-semibold uppercase tracking-[0.06em] ${REGIME_TONE[market.label].chip}`}
            >
              {market.label}
            </span>
            <span>
              {market.bullish} of 4 bullish on {market.product} · Tom focuses on {market.focus.join(', ')}
            </span>
          </div>
        )}
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Net liq" value={moneyCompact(s.net_liquidating_value)} sub={`${s.open_strategies} open`} />
        <StatTile
          label="Today"
          value={day === null ? '—' : money(day, { sign: true, cents: false })}
          tone={day === null ? 'muted' : day >= 0 ? 'profit' : 'loss'}
          sub="net liq against the last close"
        />
        <StatTile
          label="Open P&L"
          value={open === null ? '—' : money(open, { sign: true, cents: false })}
          tone={open === null ? 'muted' : open >= 0 ? 'profit' : 'loss'}
          sub="what you hold, not banked"
        />
        <StatTile
          label="Banked this month"
          value={banked === null ? '—' : money(banked, { sign: true, cents: false })}
          tone={banked === null ? 'muted' : banked >= 0 ? 'profit' : 'loss'}
          sub={
            monthShare === null
              ? 'closed trades, net of fees'
              : `${thisMonth.length} closed · ${pct(monthShare, 1)} of net liq · Tom aims for 2–3%`
          }
        />
      </section>

      <Decisions
        views={views.data}
        onPick={onOpen}
        ignored={hidden}
        onIgnored={async (next) => {
          const live = new Set(views.data!.map((v) => v.strategy.id))
          const kept = Object.fromEntries(Object.entries(next).filter(([id]) => live.has(id)))
          setIgnored(kept)
          await api.setSetting('ignored_decisions', JSON.stringify(kept))
          settings.reload()
        }}
      />

      {tom.error && !tom.data ? (
        <ErrorPanel error={tom.error} onRetry={tom.reload} />
      ) : !tom.data ? (
        <Loading label="Measuring the book against Tom's plan" />
      ) : (
        <>
          <section>
            <SectionHeading title="Fix these first" hint="the gaps with the most money at stake" />
            {tom.data.fixes.length === 0 ? (
              <Empty title="Nothing stands out. The book is inside Tom's rules." />
            ) : (
              <ol className="stagger grid gap-4 md:grid-cols-3">
                {tom.data.fixes.map((f, i) => (
                  <li key={f.title} className={`${card} lift flex flex-col px-6 py-5`}>
                    <div className="flex items-center gap-3">
                      <span className="figure flex h-8 w-8 items-center justify-center rounded-full bg-danger-soft text-[16px] text-danger">
                        {i + 1}
                      </span>
                      <span className="label">{FIX_LABEL[f.section] ?? 'Position'}</span>
                    </div>
                    <h3 className="display mt-3 text-[19px] leading-snug">{f.title}</h3>
                    <p className="mt-2 flex-1 text-[14px] leading-relaxed text-muted">{f.detail}</p>
                    <button
                      onClick={() => onTab('plan')}
                      className="mt-4 self-start text-[14px] font-medium text-accent hover:underline"
                    >
                      See it in the plan →
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section>
            <SectionHeading
              title="Your book against Tom's limits"
              hint={`buying power, delta, theta and vega · updated ${relativeTime(tom.data.as_of)}`}
            />
            <div className="stagger grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {tom.data.gauges.map((g) => (
                <GaugeCard key={g.key} g={g} />
              ))}
            </div>
            <Allocation
              total={tom.data.allocation_total}
              rows={tom.data.strategy_bp}
              positions={tom.data.positions}
              netLiq={num(tom.data.net_liq)}
            />
          </section>
        </>
      )}

      <section>
        <SectionHeading
          title="Ideas that fit"
          hint="setups ready on Tom's rules that your book has room for"
          right={
            <button
              onClick={() => onTab('scanner')}
              className="rounded-sm border border-line px-3 py-1 text-[13px] text-muted hover:bg-hover hover:text-ink"
            >
              All setups
            </button>
          }
        />
        {scan.error && !scan.data ? (
          <ErrorPanel error={scan.error} onRetry={scan.reload} />
        ) : !scan.data ? (
          <Loading label="Reading the charts — this takes a little while" />
        ) : ideas.length === 0 ? (
          <Empty title="No setup is ready and fits your room today." hint="Waiting is a position too." />
        ) : (
          <ul className="stagger grid gap-4 md:grid-cols-3">
            {ideas.map((c) => (
              <Idea key={`${c.setup}-${c.symbol}`} c={c} />
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
