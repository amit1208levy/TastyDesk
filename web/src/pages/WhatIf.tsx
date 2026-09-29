import { useEffect, useRef, useState } from 'react'
import { ErrorPanel, Loading, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { decimals, money, num, pct, signedClass } from '../lib/format'
import type { ScenarioResult, ScenarioRow } from '../types'

/* The book, priced under conditions you choose.

   The payoff diagram answers one question — where does this land at expiry —
   and refuses the rest, because an option is worth more than its intrinsic
   value right up to the end. This page is the rest: what everything is worth
   if the market moves, if a week goes by, if volatility doubles or collapses.

   Three dials, because those are the three things that move an option's
   price. Each position starts from exactly the P&L on its row — every leg is
   priced at the volatility its own mark implies — so with the dials at zero
   the book is what the Positions tab says it is, and whatever the dials do
   after that is the scenario and nothing else. */

// Price moves are fractions; volatility is a multiple of VIX now.
const PRESETS: { label: string; price: number; vix: number; days: number }[] = [
  { label: 'Market −5%', price: -0.05, vix: 1, days: 0 },
  { label: 'Market +5%', price: 0.05, vix: 1, days: 0 },
  { label: 'Crash: −10%, VIX doubles', price: -0.1, vix: 2, days: 0 },
  { label: 'A quiet week', price: 0, vix: 0.9, days: 7 },
  { label: 'Two weeks', price: 0, vix: 1, days: 14 },
]

// /RTY at 2,830 needs no decimals; /ZB at 103.16 needs two.
function level(n: number): string {
  const digits = Math.abs(n) >= 1000 ? 0 : 2
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function Dial({
  label,
  value,
  min,
  max,
  step,
  show,
  sub,
  ends,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  show: string
  sub?: string
  ends: [string, string]
  onChange: (v: number) => void
}) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] uppercase tracking-wider text-muted">{label}</span>
        <span className="text-right">
          <span className="figure text-[22px] font-semibold">{show}</span>
          {sub && <span className="ml-2 text-[14px] text-muted">{sub}</span>}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 h-1 w-full cursor-pointer appearance-none rounded-full bg-sunken accent-accent"
      />
      <div className="mt-1 flex justify-between text-[12px] text-faint">
        <span>{ends[0]}</span>
        <span>{ends[1]}</span>
      </div>
    </label>
  )
}

function Choice({
  on,
  onClick,
  children,
}: {
  on: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-sm border px-3 py-1.5 text-[14px] transition-colors ${
        on
          ? 'border-accent/50 bg-accent-soft text-accent'
          : 'border-line text-muted hover:bg-hover hover:text-ink'
      }`}
    >
      {children}
    </button>
  )
}

export function WhatIf() {
  // One position, or the whole book.
  const [selected, setSelected] = useState<string | null>(null)
  const [book, setBook] = useState<ScenarioRow[]>([])
  const [price, setPrice] = useState(0)
  // Volatility as a VIX level; null until VIX is known, then VIX now.
  const [vixTo, setVixTo] = useState<number | null>(null)
  const [days, setDays] = useState(0)
  // Move SPY and let each product follow by its beta, or move the product itself.
  const [move, setMove] = useState<'spy' | 'underlying'>('spy')
  const [data, setData] = useState<ScenarioResult | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  const vixNow = num(data?.vix ?? null)
  const spyNow = num(data?.spy ?? null)
  const vixTarget = vixTo ?? vixNow
  // Every option's volatility moves by the proportion VIX moves by.
  const iv = vixNow && vixTarget ? vixTarget / vixNow - 1 : 0
  const byBeta = move === 'spy'

  // Which request is the latest. Answers can come back out of order — a
  // whole-book pricing is slower than one position — and an older answer
  // arriving last used to overwrite the one for where the dials now are.
  const latest = useRef(0)

  // A drag fires dozens of changes; the book is re-priced once it settles.
  useEffect(() => {
    const t = setTimeout(() => {
      const ticket = ++latest.current
      setBusy(true)
      api
        .scenario(price, iv, days, byBeta, selected)
        .then((d) => {
          if (ticket !== latest.current) return
          setData(d)
          if (selected === null) setBook(d.positions)
          setError(null)
        })
        .catch((e) => setError(e instanceof Error ? e : new Error(String(e))))
        .finally(() => setBusy(false))
    }, 180)
    return () => clearTimeout(t)
  }, [price, iv, days, byBeta, selected])

  if (error && !data) return <ErrorPanel error={error} onRetry={() => setPrice((p) => p)} />
  if (!data) return <Loading label="Pricing your book" />

  const change = num(data.change)
  const untouched = price === 0 && Math.abs(iv) < 1e-9 && days === 0
  const chosen = selected === null ? null : book.find((r) => r.id === selected) ?? null
  const base =
    move === 'spy' ? spyNow : chosen !== null ? num(chosen.price) : null
  const priceLabel =
    move === 'spy' ? 'SPY' : chosen !== null ? chosen.underlying : 'Every product'
  const vixMax = Math.max(60, Math.ceil((vixNow ?? 20) * 2))

  return (
    <div className="space-y-5">
      <SectionHeading
        title="What if"
        hint="one position or the whole book, priced under conditions you choose"
      />

      {/* Which position. The whole book first, then each trade. */}
      <div className="flex flex-wrap gap-2">
        <Choice on={selected === null} onClick={() => setSelected(null)}>
          Whole book
        </Choice>
        {book.map((r) => (
          <Choice key={r.id} on={selected === r.id} onClick={() => setSelected(r.id)}>
            <span className="font-medium">{r.underlying}</span>{' '}
            <span className="text-[13px] opacity-80">{r.name}</span>
          </Choice>
        ))}
      </div>

      <section className="sheened rounded-card border border-line bg-raised p-5 shadow-[var(--shadow-sm)]">
        <div className="mb-5 flex flex-wrap items-center gap-2 text-[14px]">
          <span className="text-muted">Move</span>
          <Choice on={move === 'spy'} onClick={() => setMove('spy')}>
            SPY{spyNow !== null ? ` (${level(spyNow)})` : ''}, each product by its beta
          </Choice>
          <Choice on={move === 'underlying'} onClick={() => setMove('underlying')}>
            {chosen !== null
              ? `${chosen.underlying} itself${num(chosen.price) !== null ? ` (${level(num(chosen.price)!)})` : ''}`
              : 'each product itself, all by the same %'}
          </Choice>
        </div>

        <div className="grid gap-6 md:grid-cols-3">
          <Dial
            label={priceLabel}
            value={price}
            min={-0.2}
            max={0.2}
            step={0.0025}
            show={base !== null ? level(base * (1 + price)) : pct(price, 1, true)}
            sub={base !== null ? pct(price, 1, true) : undefined}
            ends={
              base !== null
                ? [level(base * 0.8), level(base * 1.2)]
                : [pct(-0.2, 0, true), pct(0.2, 0, true)]
            }
            onChange={setPrice}
          />
          {vixNow !== null && vixTarget !== null ? (
            <Dial
              label="VIX"
              value={vixTarget}
              min={9}
              max={vixMax}
              step={0.25}
              show={vixTarget.toFixed(2)}
              sub={
                Math.abs(iv) < 1e-9
                  ? `now`
                  : `${iv > 0 ? '+' : ''}${(iv * 100).toFixed(0)}% vol`
              }
              ends={['9', String(vixMax)]}
              onChange={setVixTo}
            />
          ) : (
            <div className="text-[14px] text-muted">VIX is not quoted right now.</div>
          )}
          <Dial
            label="Days forward"
            value={days}
            min={0}
            max={60}
            step={1}
            show={days === 0 ? 'today' : `+${days}d`}
            ends={['today', '60 days']}
            onChange={setDays}
          />
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => {
                setPrice(p.price)
                setVixTo(vixNow === null ? null : Math.round(vixNow * p.vix * 4) / 4)
                setDays(p.days)
              }}
              className="rounded-sm border border-line px-3 py-1.5 text-[14px] text-muted transition-colors hover:bg-hover hover:text-ink"
            >
              {p.label}
            </button>
          ))}
          {!untouched && (
            <button
              onClick={() => {
                setPrice(0)
                setVixTo(null)
                setDays(0)
              }}
              className="ml-auto rounded-sm border border-accent/50 bg-accent-soft px-3 py-1.5 text-[14px] text-accent"
            >
              Back to now
            </button>
          )}
        </div>

        <p className="mt-4 text-[14px] leading-relaxed text-muted">
          {move === 'spy'
            ? 'You move SPY and each product follows by its own beta — a 10% fall in SPY moves /ZB by about 5% and wheat by almost nothing, which is closer to what a market fall does than moving everything alike.'
            : chosen !== null
              ? `You move ${chosen.underlying} directly, to the price on the dial.`
              : 'Every product moves by the same percentage. Useful for one product at a time; across a mixed book it treats a 10% move in bonds as ordinary, which it is not.'}{' '}
          VIX sets volatility: every option’s volatility moves by the same proportion VIX does, so
          VIX from {vixNow !== null ? vixNow.toFixed(0) : '16'} to{' '}
          {vixNow !== null ? (vixNow * 1.5).toFixed(0) : '24'} takes a 20% option to 30%. Days
          forward runs the clock; a leg that expires on the way settles at intrinsic. Nothing else
          moves, so this is the same position under stated conditions, not a forecast.
        </p>
      </section>

      <section className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-card border border-line bg-raised p-4">
          <div className="text-[12px] uppercase tracking-wider text-muted">
            {chosen !== null ? 'This position now' : 'Book now'}
          </div>
          <div className={`figure text-[26px] font-semibold ${signedClass(data.now)}`}>
            {money(data.now, { sign: true, cents: false })}
          </div>
        </div>
        <div className="rounded-card border border-line bg-raised p-4">
          <div className="text-[12px] uppercase tracking-wider text-muted">Under this scenario</div>
          <div className={`figure text-[26px] font-semibold ${signedClass(data.then)}`}>
            {money(data.then, { sign: true, cents: false })}
          </div>
        </div>
        <div className="rounded-card border border-accent/40 bg-raised p-4">
          <div className="text-[12px] uppercase tracking-wider text-muted">The difference</div>
          <div className={`figure text-[26px] font-semibold ${signedClass(change)}`}>
            {money(change, { sign: true, cents: false })}
          </div>
          {busy && <div className="text-[13px] text-faint">repricing…</div>}
        </div>
      </section>

      {data.unpriced > 0 && (
        <p className="text-[14px] text-warn">
          {data.unpriced} position{data.unpriced === 1 ? '' : 's'} could not be priced — a leg with
          no mark and no implied volatility — so the book totals are left blank rather than shown
          short.
        </p>
      )}

      <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
        <table className="w-max text-[16px]">
          <thead>
            <tr className="border-b border-line text-left text-[13px] uppercase tracking-wider text-muted">
              <th className="py-3 pl-4 pr-5 font-medium">Position</th>
              <th className="py-3 pr-5 text-right font-medium">Price now → then</th>
              <th className="py-3 pr-5 text-right font-medium">DTE</th>
              <th className="py-3 pr-5 text-right font-medium">Delta now → then</th>
              <th className="py-3 pr-5 text-right font-medium">P&L now</th>
              <th className="py-3 pr-5 text-right font-medium">P&L then</th>
              <th className="py-3 pr-4 text-right font-medium">Change</th>
            </tr>
          </thead>
          <tbody className="num">
            {data.positions.map((r) => (
              <tr key={r.id} className="border-b border-line/60 last:border-0 hover:bg-hover">
                <td className="py-3 pl-4 pr-5">
                  <div className="font-medium">{r.underlying}</div>
                  <div className="text-[13px] text-muted">{r.name}</div>
                </td>
                <td className="py-3 pr-5 text-right text-muted">
                  {decimals(r.price, 2)}
                  {!untouched && price !== 0 && (
                    <>
                      <span className="text-faint"> → </span>
                      <span className="text-ink">{decimals(r.price_then, 2)}</span>
                    </>
                  )}
                </td>
                <td className="py-3 pr-5 text-right text-muted">
                  {r.dte === null ? '—' : days > 0 ? `${Math.max(r.dte - days, 0)}d` : `${r.dte}d`}
                </td>
                <td className="py-3 pr-5 text-right">
                  <DeltaMove from={r.delta_now} to={r.delta_then} moved={!untouched} />
                </td>
                <td className={`py-3 pr-5 text-right ${signedClass(r.now)}`}>
                  {money(r.now, { sign: true, cents: false })}
                </td>
                <td className={`py-3 pr-5 text-right font-medium ${signedClass(r.then)}`}>
                  {money(r.then, { sign: true, cents: false })}
                </td>
                <td className={`py-3 pr-4 text-right font-semibold ${signedClass(r.change)}`}>
                  {money(r.change, { sign: true, cents: false })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* One position: each strike on its own, because that is where delta
          changes. A strike far from the price barely moves with it; the same
          strike once price reaches it moves half as much as the underlying. */}
      {chosen !== null && data.positions[0] && data.positions[0].legs.length > 0 && (
        <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
          <div className="px-4 pt-4 text-[13px] font-semibold uppercase tracking-wider text-muted">
            Each strike — how its delta changes with the price
          </div>
          <table className="w-max text-[16px]">
            <thead>
              <tr className="border-b border-line text-left text-[13px] uppercase tracking-wider text-muted">
                <th className="py-3 pl-4 pr-5 font-medium">Leg</th>
                <th className="py-3 pr-5 text-right font-medium">Price vs strike</th>
                <th className="py-3 pr-5 text-right font-medium">Delta per contract</th>
                <th className="py-3 pr-4 text-right font-medium">Position delta</th>
              </tr>
            </thead>
            <tbody className="num">
              {data.positions[0].legs.map((l, i) => {
                const k = num(l.strike)
                const a = num(l.underlying_now)
                const b = num(l.underlying_then)
                const away = (s: number | null) =>
                  k === null || s === null ? '—' : `${s - k >= 0 ? '+' : ''}${level(s - k)}`
                return (
                  <tr key={i} className="border-b border-line/60 last:border-0">
                    <td className="py-3 pl-4 pr-5">
                      <span
                        className={`mr-2 inline-block w-12 rounded px-1 py-0.5 text-center text-[12px] uppercase ${
                          l.side === 'short' ? 'bg-accent-soft text-accent' : 'bg-sunken text-muted'
                        }`}
                      >
                        {l.side}
                      </span>
                      {Number(l.quantity)} ×{' '}
                      {k === null ? l.right : `${level(k)} ${l.right === 'C' ? 'call' : 'put'}`}
                    </td>
                    <td className="py-3 pr-5 text-right text-muted">
                      {k === null ? (
                        '—'
                      ) : (
                        <>
                          {away(a)}
                          {!untouched && price !== 0 && (
                            <>
                              <span className="text-faint"> → </span>
                              <span className="text-ink">{away(b)}</span>
                            </>
                          )}
                        </>
                      )}
                    </td>
                    <td className="py-3 pr-5 text-right">
                      <DeltaMove from={l.delta_now} to={l.delta_then} moved={!untouched} plain />
                    </td>
                    <td className="py-3 pr-4 text-right">
                      <DeltaMove from={l.position_delta_now} to={l.position_delta_then} moved={!untouched} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/* A delta and where the scenario takes it. The arrow only when something
   moved; colour on the result only when it is a position's delta, where the
   sign says which way you are exposed. */
function DeltaMove({
  from,
  to,
  moved,
  plain = false,
}: {
  from: string | null
  to: string | null
  moved: boolean
  plain?: boolean
}) {
  const a = num(from)
  const b = num(to)
  if (a === null) return <span className="text-faint">—</span>
  const show = (n: number) => `${!plain && n > 0 ? '+' : ''}${n.toFixed(2)}`
  const changed = moved && b !== null && Math.abs(b - a) >= 0.005
  return (
    <span>
      <span className={changed ? 'text-muted' : plain ? 'text-ink' : signedClass(a)}>{show(a)}</span>
      {changed && (
        <>
          <span className="text-faint"> → </span>
          <span className={`font-semibold ${plain ? 'text-ink' : signedClass(b)}`}>{show(b!)}</span>
        </>
      )}
    </span>
  )
}
