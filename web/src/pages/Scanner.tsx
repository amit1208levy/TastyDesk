import { useState, type ReactNode } from 'react'
import { Empty, ErrorPanel, Loading, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, num, pct, shortDate, relativeTime, EM_DASH } from '../lib/format'
import type { ScanCandidate, ScanCheck, ScanFit, ScanPlan, ScanResult } from '../types'

/* Tom's scanner: the market read through the entry rules of his 2026 plan.

   Every card is one setup on one product with its checklist laid out — the
   chart, the volatility, the calendar — and, where it passes, the trade
   written in real strikes from the live chain, the stop and target in
   dollars, a size already capped by his limits for this account, and how the
   book would read with it on. The backend (core/scanner.py) decides all of
   it; this file draws it, and says plainly that a candidate is a rule match
   rather than advice. */

const card = 'surface sheened'

const STATUS: Record<ScanCandidate['status'], { label: string; chip: string; dot: string }> = {
  ready: { label: 'Passes every check', chip: 'bg-profit-soft text-ok', dot: 'bg-ok' },
  almost: { label: 'One thing short', chip: 'bg-tested-soft text-tested', dot: 'bg-tested' },
  watch: { label: 'Not yet', chip: 'bg-sunken text-muted', dot: 'bg-line-strong' },
}

const REGIME: Record<string, string> = {
  Bullish: 'bg-profit-soft text-ok',
  Neutral: 'bg-watch-soft text-watch',
  Bearish: 'bg-danger-soft text-danger',
}

const FILTERS: { id: string; label: string; setups: string[] }[] = [
  { id: 'all', label: 'Everything', setups: [] },
  { id: 'core', label: 'Core: 11x & PMCC', setups: ['11x', 'pmcc'] },
  { id: 'strangle', label: 'Strangles', setups: ['strangle'] },
  { id: 'puts', label: 'Puts & spreads', setups: ['naked_put', 'es_120', 'spx_pcs'] },
]

function Chip({ className, children }: { className: string; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12px] font-semibold ${className}`}>
      {children}
    </span>
  )
}

function CheckRow({ c }: { c: ScanCheck }) {
  const mark = c.ok === true ? '✓' : c.ok === false ? '✕' : '–'
  const tone =
    c.ok === true ? 'bg-profit-soft text-ok' : c.ok === false ? (c.hard ? 'bg-danger-soft text-danger' : 'bg-tested-soft text-tested') : 'bg-sunken text-faint'
  return (
    <li className="flex items-start gap-2.5 py-1">
      <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${tone}`}>{mark}</span>
      <span className="min-w-0 text-[14px] leading-snug">
        <span className="text-ink">{c.label}</span>
        {c.hard && c.ok === false && <span className="text-danger"> · must pass</span>}
        <span className="block text-[12px] text-faint">{c.detail}</span>
      </span>
    </li>
  )
}

/* Strikes and premiums as the exchange writes them: a yen future's put is
   struck at 0.00605 and costs 0.000007, and two decimals would print both as
   nothing. */
function exact(v: string | null): string {
  const n = num(v)
  if (n === null) return EM_DASH
  if (Number.isInteger(n)) return n.toLocaleString('en-US')
  if (Math.abs(n) >= 1) return n.toLocaleString('en-US', { maximumFractionDigits: 4 })
  return String(Number(n.toPrecision(4)))
}

function legLine(l: ScanPlan['legs'][number]) {
  return `${l.action} ${l.quantity} ${l.right === 'P' ? 'put' : 'call'}${l.quantity > 1 ? 's' : ''}`
}

function Trade({ plan, fit }: { plan: ScanPlan; fit: ScanFit | null }) {
  const credit = num(plan.credit) ?? 0
  const lots = plan.lots
  return (
    <div className="mt-4 rounded-sm border border-line bg-raised-solid/60 px-4 py-3">
      <div className="flex items-baseline justify-between gap-3">
        <div className="label">The trade, by Tom's recipe</div>
        <span className="num text-[13px] text-muted">
          {credit >= 0 ? 'credit' : 'debit'} {money(Math.abs(credit), { cents: false })} a lot
        </span>
      </div>
      <table className="mt-2 w-full text-[14px]">
        <tbody className="num">
          {plan.legs.map((l, i) => (
            <tr key={i} className="border-t border-line/60 first:border-0">
              {/* The slack goes to this words column, never between the figures. */}
              <td className={`w-full whitespace-nowrap py-1.5 pr-3 font-medium ${l.action === 'Sell' ? 'text-ink' : 'text-muted'}`}>
                {legLine(l)}
              </td>
              <td className="whitespace-nowrap py-1.5 pr-4 text-right font-semibold">{exact(l.strike)}</td>
              <td className="whitespace-nowrap py-1.5 pr-4 text-muted">
                {shortDate(l.expiry)} · {l.dte}d
              </td>
              <td
                className="whitespace-nowrap py-1.5 pr-4 text-right text-muted"
                title={l.estimated ? 'From a model: the broker sent no greeks for this strike' : undefined}
              >
                {l.delta === null ? EM_DASH : `${Math.abs(num(l.delta) ?? 0).toFixed(2)}Δ`}
                {l.estimated && '*'}
              </td>
              <td className="whitespace-nowrap py-1.5 text-right">{exact(l.mark)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <dl className="mt-3 grid gap-x-4 gap-y-2 border-t border-line pt-3 text-[13px] sm:grid-cols-2">
        <div>
          <dt className="text-faint">Size</dt>
          <dd className={`font-semibold ${lots < 1 ? 'text-tested' : 'text-ink'}`}>
            {lots < 1 ? 'Too big for your account' : `${lots} lot${lots === 1 ? '' : 's'}`}
            <span className="block text-[12px] font-normal text-faint">capped by {plan.lots_reason}</span>
          </dd>
        </div>
        <div>
          <dt className="text-faint">Buying power</dt>
          <dd className="font-semibold text-ink">
            {plan.bp_per_lot === null ? 'on the order ticket' : `${money(plan.bp_per_lot, { cents: false })} a lot`}
            <span className="block text-[12px] font-normal text-faint">{plan.bp_basis}</span>
          </dd>
        </div>
        <div>
          <dt className="text-faint">Stop</dt>
          <dd className="text-ink">{plan.stop}</dd>
        </div>
        <div>
          <dt className="text-faint">Target</dt>
          <dd className="text-ink">{plan.target}</dd>
        </div>
      </dl>

      {fit && <FitRow fit={fit} lots={Math.max(lots, 1)} />}
      {plan.notes.length > 0 && (
        <ul className="mt-3 space-y-1 text-[13px] text-muted">
          {plan.notes.map((n) => (
            <li key={n}>· {n}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function FitChip({ ok, label, value }: { ok: boolean | null; label: string; value: string }) {
  const tone = ok === null ? 'bg-sunken text-muted' : ok ? 'bg-profit-soft text-ok' : 'bg-danger-soft text-danger'
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] ${tone}`}>
      <span className="font-semibold">{label}</span>
      <span className="num">{value}</span>
    </span>
  )
}

function FitRow({ fit, lots }: { fit: ScanFit; lots: number }) {
  const theta = num(fit.theta_after)
  const target = num(fit.theta_target)
  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="text-[12px] text-faint">Your book with {lots} lot{lots === 1 ? '' : 's'} on</div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <FitChip ok={fit.bp_ok} label="BP" value={fit.bp_after_share === null ? 'unknown' : `${pct(fit.bp_after_share, 0)} (≤ 50%)`} />
        <FitChip
          ok={fit.strategy_ok}
          label="Strategy"
          value={fit.strategy_after_share === null ? 'unknown' : `${pct(fit.strategy_after_share, 0)} (≤ 20%)`}
        />
        <FitChip
          ok={fit.delta_ok}
          label="Delta"
          value={fit.delta_after === null ? 'unknown' : `${(num(fit.delta_after) ?? 0) >= 0 ? '+' : ''}${Math.round(num(fit.delta_after) ?? 0)} (±${Math.round(num(fit.delta_limit) ?? 0)})`}
        />
        {/* Under Tom's theta floor is the book's state, not this trade's fault:
            grey, not red, unless the trade reaches it. */}
        <FitChip
          ok={theta === null || target === null ? null : theta >= target ? true : null}
          label="Theta"
          value={theta === null ? 'unknown' : `${money(theta, { cents: false })}/day (Tom ≥ ${money(target, { cents: false })})`}
        />
      </div>
    </div>
  )
}

function CandidateCard({ c }: { c: ScanCandidate }) {
  const s = STATUS[c.status]
  const rsiUp = c.checks.some((k) => k.detail.includes('rising'))
  return (
    <article className={`${card} lift flex h-full flex-col px-6 py-5`}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="display text-[26px] leading-none">{c.symbol}</h3>
        <span className="text-[15px] text-muted">{c.setup_name}</span>
        <span className="ml-auto flex items-center gap-1.5">
          <Chip className="bg-sunken text-muted">{c.tier === 'core' ? 'Core' : 'Spec'}</Chip>
          <Chip className={s.chip}>
            <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
            {s.label}
          </Chip>
        </span>
      </div>
      <p className="mt-2 text-[15px] leading-relaxed text-ink">{c.headline}</p>
      <div className="num mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-muted">
        <span>{c.price.toLocaleString('en-US', { maximumFractionDigits: c.price < 10 ? 4 : 2 })}</span>
        <span>
          RSI {c.rsi.toFixed(0)}
          {rsiUp ? ' ↑' : ''}
        </span>
        <span className={`rounded-full px-2 ${REGIME[c.regime] ?? ''}`}>{c.regime}</span>
        {c.iv_rank !== null && <span>IVR {Math.round((num(c.iv_rank) ?? 0) * 100)}</span>}
        {c.earnings && <span>earnings {shortDate(c.earnings)}</span>}
        <span className="text-faint">{c.source}</span>
      </div>
      <p className="mt-1.5 text-[12px] text-faint">Tom's tickers for this: {c.tom_list}</p>
      <ul className="mt-3 grid gap-x-4 sm:grid-cols-2">
        {c.checks.map((k) => (
          <CheckRow key={k.label} c={k} />
        ))}
      </ul>
      {c.alternatives.length > 0 && (
        <ul className="mt-3 space-y-1 text-[13px] text-muted">
          {c.alternatives.map((a) => (
            <li key={a}>· {a}</li>
          ))}
        </ul>
      )}
      {c.plan ? (
        <Trade plan={c.plan} fit={c.fit} />
      ) : c.status !== 'watch' ? (
        <p className="mt-4 text-[13px] text-faint">No strikes yet: the chain for this expiry could not be read.</p>
      ) : null}
    </article>
  )
}

function Room({ data }: { data: ScanResult }) {
  const bp = num(data.bp_share)
  const theta = num(data.theta)
  const target = num(data.theta_target)
  const delta = num(data.delta)
  const limit = num(data.delta_limit)
  const items = [
    {
      label: 'Buying power used',
      value: pct(bp, 0),
      sub: num(data.bp_room)! > 0 ? `${money(data.bp_room, { cents: false })} of room to Tom's 50%` : "over Tom's 50%: no new trades",
      good: bp !== null && bp <= 0.5,
    },
    {
      label: 'Delta',
      value: delta === null ? EM_DASH : `${delta >= 0 ? '+' : ''}${Math.round(delta)}`,
      sub: limit === null ? '' : `Tom's limit ±${Math.round(limit)}`,
      good: delta !== null && limit !== null && Math.abs(delta) <= limit,
    },
    {
      label: 'Theta a day',
      value: theta === null ? EM_DASH : money(theta, { cents: false }),
      sub: target === null ? '' : `Tom wants at least ${money(target, { cents: false })}`,
      good: theta !== null && target !== null && theta >= target,
    },
    {
      label: 'Vega against theta',
      value: data.vega_ratio === null ? EM_DASH : `${(num(data.vega_ratio) ?? 0).toFixed(1)}×`,
      sub: 'Tom keeps it at 1–1.5×',
      good: data.vega_ratio !== null && (num(data.vega_ratio) ?? 9) <= 1.5,
    },
  ]
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {items.map((i) => (
        <div key={i.label} className={`${card} px-5 py-4`}>
          <div className="flex items-center justify-between">
            <div className="label">{i.label}</div>
            <span className={`h-2 w-2 rounded-full ${i.good ? 'bg-ok' : 'bg-tested'}`} />
          </div>
          <div className="figure mt-1.5 text-[26px] leading-none">{i.value}</div>
          <div className="mt-1.5 text-[13px] text-muted">{i.sub}</div>
        </div>
      ))}
    </div>
  )
}

export function Scanner() {
  const [force, setForce] = useState(0)
  const [filter, setFilter] = useState('all')
  const { data, error, loading } = useAsync(() => api.scanner(force > 0), [force])

  if (error && !data) return <ErrorPanel error={error} onRetry={() => setForce((n) => n + 1)} />
  if (!data) return <Loading label="Running Tom's checklists on his own tickers — about 20 seconds the first time" />

  const chosen = FILTERS.find((f) => f.id === filter)!
  const inFilter = (c: ScanCandidate) => chosen.setups.length === 0 || chosen.setups.includes(c.setup)
  const live = data.candidates.filter((c) => c.status !== 'watch' && inFilter(c))
  const watch = data.candidates.filter((c) => c.status === 'watch' && inFilter(c))
  const market = data.market

  return (
    <div className="space-y-12">
      <section className={`${card} relative overflow-hidden px-8 py-9 sm:px-10`}>
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-32 h-96 w-96 rounded-full opacity-25 blur-3xl"
          style={{ background: 'radial-gradient(closest-side, var(--accent), transparent)' }}
        />
        <div className="relative flex flex-wrap items-start justify-between gap-6">
          <div className="min-w-0 max-w-[70ch]">
            <div className="label text-accent">Tom King · 2026 trading plan</div>
            <h1 className="display mt-3 text-[clamp(30px,4vw,46px)] leading-[1.05]">Tom's scanner</h1>
            <p className="mt-3 text-[17px] leading-relaxed text-muted">
              His entry checklists — chart, RSI, MACD, trend, IV rank, earnings — on only the tickers he names for each
              setup. The contract is chosen by your account's size, and what passes comes with strikes from the live
              chain, sized to his caps.
            </p>
            {market && (
              <div className="mt-5 inline-flex flex-wrap items-center gap-x-3 gap-y-1 rounded-full border border-line bg-raised px-4 py-2 text-[14px]">
                <Chip className={REGIME[market.label]}>{market.label}</Chip>
                <span className="text-muted">
                  SPY {market.bullish} of 4 bullish · Tom focuses on {market.focus.join(', ')}
                </span>
              </div>
            )}
          </div>
          <div className="flex flex-col items-end gap-2">
            <button
              className="btn btn-primary"
              disabled={loading}
              onClick={() => setForce((n) => n + 1)}
            >
              {loading ? 'Scanning…' : 'Scan again'}
            </button>
            <span className="text-[12px] text-faint">
              scanned {relativeTime(data.as_of)} in {Math.round(data.seconds)}s
            </span>
          </div>
        </div>
      </section>

      <section>
        <SectionHeading title="Room in your book" hint="what a new trade has to fit into" />
        <Room data={data} />
      </section>

      <section>
        <SectionHeading title="What passes Tom's checklists" hint={`${live.length} candidate${live.length === 1 ? '' : 's'} · best first`} />
        <div className="mb-4 flex flex-wrap gap-1.5" role="tablist" aria-label="Filter by setup">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              role="tab"
              aria-selected={filter === f.id}
              onClick={() => setFilter(f.id)}
              className={`rounded-full px-3.5 py-1.5 text-[14px] ${
                filter === f.id ? 'bg-accent font-medium text-white' : 'bg-sunken text-muted hover:text-ink'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        {live.length === 0 ? (
          <Empty
            title="Nothing passes Tom's checklists right now."
            hint={market?.label === 'Bearish' ? "The market is bearish: Tom's plan says reduce buying power rather than add." : undefined}
          />
        ) : (
          <div className={`stagger grid gap-4 xl:grid-cols-2 ${loading ? 'opacity-60' : ''}`}>
            {live.map((c) => (
              <CandidateCard key={`${c.symbol}-${c.setup}`} c={c} />
            ))}
          </div>
        )}
      </section>

      {watch.length > 0 && (
        <section>
          <SectionHeading title="On the watch list" hint="close, but a must-pass check fails" />
          <div className={`${card} divide-y divide-line`}>
            {watch.map((c) => {
              const failing = c.checks.filter((k) => k.ok === false)
              return (
                <div key={`${c.symbol}-${c.setup}`} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-3">
                  <span className="w-20 font-semibold">{c.symbol}</span>
                  <span className="w-44 text-[14px] text-muted">{c.setup_name}</span>
                  <span className="num w-20 text-[13px] text-faint">RSI {c.rsi.toFixed(0)}</span>
                  <span className="min-w-0 flex-1 text-[13px] text-muted">
                    {failing.map((k) => k.label.toLowerCase()).join(' · ')}
                  </span>
                </div>
              )
            })}
          </div>
        </section>
      )}

      <footer className="text-[13px] leading-relaxed text-faint">
        Candidates pass the checks Tom King's 2026 plan writes down; they are not advice, and the decision is yours.
        Only the tickers Tom names for each setup in his plan, strategy sheets and videos are scanned. Simplified
        where data runs out: "quality" is a market cap over $10B, the weekly trend is read from the daily 21 and 50
        EMAs, and buying power for futures options is only known on the order ticket. Charts: Yahoo Finance daily closes. Strikes, deltas and prices: tastytrade, live.
        {data.missing.length > 0 && <> No chart for {data.missing.join(', ')}.</>}
      </footer>
    </div>
  )
}
