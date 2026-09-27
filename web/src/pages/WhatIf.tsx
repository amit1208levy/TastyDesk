import { useEffect, useState } from 'react'
import { ErrorPanel, Loading, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { decimals, money, num, pct, signedClass } from '../lib/format'
import type { ScenarioResult } from '../types'

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

const PRESETS: { label: string; price: number; iv: number; days: number }[] = [
  { label: 'Market −5%', price: -0.05, iv: 0, days: 0 },
  { label: 'Market +5%', price: 0.05, iv: 0, days: 0 },
  { label: 'Crash: −10%, vol ×1.5', price: -0.1, iv: 0.5, days: 0 },
  { label: 'A quiet week', price: 0, iv: -0.1, days: 7 },
  { label: 'Two weeks', price: 0, iv: 0, days: 14 },
]

function Dial({
  label,
  value,
  min,
  max,
  step,
  show,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  show: string
  onChange: (v: number) => void
}) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between">
        <span className="text-[13px] uppercase tracking-wider text-muted">{label}</span>
        <span className="figure text-[22px] font-semibold">{show}</span>
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
        <span>{min === 0 ? 'today' : pct(min, 0, true)}</span>
        <span>{label === 'Days forward' ? `${max} days` : pct(max, 0, true)}</span>
      </div>
    </label>
  )
}

export function WhatIf() {
  const [price, setPrice] = useState(0)
  const [iv, setIv] = useState(0)
  const [days, setDays] = useState(0)
  // Move each product by its beta to SPY, or all by the same percentage.
  const [byBeta, setByBeta] = useState(true)
  const [data, setData] = useState<ScenarioResult | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  // A drag fires dozens of changes; the book is re-priced once it settles.
  useEffect(() => {
    const t = setTimeout(() => {
      setBusy(true)
      api
        .scenario(price, iv, days, byBeta)
        .then((d) => {
          setData(d)
          setError(null)
        })
        .catch((e) => setError(e instanceof Error ? e : new Error(String(e))))
        .finally(() => setBusy(false))
    }, 180)
    return () => clearTimeout(t)
  }, [price, iv, days, byBeta])

  if (error && !data) return <ErrorPanel error={error} onRetry={() => setPrice((p) => p)} />
  if (!data) return <Loading label="Pricing your book" />

  const change = num(data.change)
  const untouched = price === 0 && iv === 0 && days === 0

  return (
    <div className="space-y-5">
      <SectionHeading
        title="What if"
        hint="your open positions, priced under conditions you choose"
      />

      <section className="sheened rounded-card border border-line bg-raised p-5 shadow-[var(--shadow-sm)]">
        <div className="grid gap-6 md:grid-cols-3">
          <Dial
            label={byBeta ? 'SPY move' : 'Price'}
            value={price}
            min={-0.2}
            max={0.2}
            step={0.005}
            show={pct(price, 1, true)}
            onChange={setPrice}
          />
          <Dial
            label="Volatility"
            value={iv}
            min={-0.5}
            max={1}
            step={0.05}
            show={pct(iv, 0, true)}
            onChange={setIv}
          />
          <Dial
            label="Days forward"
            value={days}
            min={0}
            max={60}
            step={1}
            show={days === 0 ? 'today' : `+${days}d`}
            onChange={setDays}
          />
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              onClick={() => {
                setPrice(p.price)
                setIv(p.iv)
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
                setIv(0)
                setDays(0)
              }}
              className="ml-auto rounded-sm border border-accent/50 bg-accent-soft px-3 py-1.5 text-[14px] text-accent"
            >
              Back to now
            </button>
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-[14px]">
          <span className="text-muted">Move each product</span>
          {[
            { on: true, label: 'with SPY, by its beta' },
            { on: false, label: 'all by the same %' },
          ].map((o) => (
            <button
              key={o.label}
              onClick={() => setByBeta(o.on)}
              className={`rounded-sm border px-3 py-1 transition-colors ${
                byBeta === o.on
                  ? 'border-accent/50 bg-accent-soft text-accent'
                  : 'border-line text-muted hover:bg-hover hover:text-ink'
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>

        <p className="mt-3 text-[14px] leading-relaxed text-muted">
          {byBeta
            ? 'The price dial is a move in SPY, and each product follows by its own beta — a 10% fall in SPY moves /ZB by about 5% and wheat by almost nothing, which is closer to what a market fall does than moving everything alike.'
            : 'Every product moves by the same percentage. Useful for one product at a time; across a mixed book it treats a 10% move in bonds as ordinary, which it is not.'}{' '}
          Volatility is relative to what each
          leg trades at now — +50% takes a 20% option to 30%. Days forward runs the clock; a leg
          that expires on the way settles at intrinsic. Nothing else moves, so this is the same
          book under stated conditions, not a forecast.
        </p>
      </section>

      <section className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-card border border-line bg-raised p-4">
          <div className="text-[12px] uppercase tracking-wider text-muted">Book now</div>
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
        <table className="w-full min-w-[720px] text-[16px]">
          <thead>
            <tr className="border-b border-line text-left text-[13px] uppercase tracking-wider text-muted">
              <th className="py-3 pl-4 pr-5 font-medium">Position</th>
              <th className="py-3 pr-5 text-right font-medium">Price now → then</th>
              <th className="py-3 pr-5 text-right font-medium">DTE</th>
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
    </div>
  )
}
