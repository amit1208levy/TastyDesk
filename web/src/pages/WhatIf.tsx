import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ErrorPanel, Loading, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { money, num, pct, shortDate, signedClass } from '../lib/format'
import { useWidth } from '../lib/useMeasure'
import type { ScenarioCurve, ScenarioResult, ScenarioRow } from '../types'

/* The book, priced under conditions you choose — and seen changing as you
   choose them.

   The first version put the dials at the top and the answer underneath, so
   every move of a dial meant scrolling down to find out what it did, and by
   then the connection between the two was gone. Now the dials stay pinned
   beside the result, the big number moves as you drag, and the P&L curve is
   drawn for every price at once: dragging price slides a dot along a line
   that is already on screen, with no wait. Volatility and time redraw the
   line itself, against today's line left in place underneath, so what they
   did is the gap between the two. You can also drag on the chart.

   Every position starts from exactly the P&L on its row — each leg is priced
   at the volatility its own mark implies — so with the dials at rest the
   picture is what the Positions tab says, and whatever moves after that is
   the scenario and nothing else. */

// Price moves are fractions; volatility is a multiple of VIX now.
const PRESETS: { label: string; price: number; vix: number; days: number }[] = [
  { label: 'Market −5%', price: -0.05, vix: 1, days: 0 },
  { label: 'Market +5%', price: 0.05, vix: 1, days: 0 },
  { label: 'Crash: −10%, VIX doubles', price: -0.1, vix: 2, days: 0 },
  { label: 'A quiet week', price: 0, vix: 0.9, days: 7 },
  { label: 'Two weeks', price: 0, vix: 1, days: 14 },
]

const RANGE = 0.2
const STEP = 0.0025

// /RTY at 2,830 needs no decimals; /ZB at 103.16 needs two.
function level(n: number): string {
  const digits = Math.abs(n) >= 1000 ? 0 : 2
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function daysUntil(iso: string): number {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.round((new Date(`${iso}T00:00:00`).getTime() - today.getTime()) / 86_400_000)
}

function dateIn(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

// /ZBZ6 and /ZBH7 are one product in two months; a stock is its own product.
function productOf(underlying: string): string {
  return underlying.startsWith('/') ? underlying.replace(/[FGHJKMNQUVXZ]\d{1,2}$/, '') : underlying
}

/* A number that travels to its new value instead of jumping there. The
   motion is the point: it is what says "this changed because you did that". */
function useTween(target: number | null, ms = 260): number | null {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    if (target === null) {
      setShown(null)
      from.current = null
      return
    }
    const start = from.current ?? target
    const began = performance.now()
    let frame = 0
    const tick = (at: number) => {
      const k = Math.min(1, (at - began) / ms)
      const eased = 1 - Math.pow(1 - k, 3)
      const value = start + (target - start) * eased
      from.current = value
      setShown(value)
      if (k < 1) frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    // Animation frames stop in a background tab; the number still has to
    // arrive, so a timer finishes the job if the frames never came.
    const settle = setTimeout(() => {
      from.current = target
      setShown(target)
    }, ms + 40)
    return () => {
      cancelAnimationFrame(frame)
      clearTimeout(settle)
    }
  }, [target, ms])
  return shown
}

/* The curve's value at any price move, between its points. */
function at(curve: ScenarioCurve | null, shift: number, key: 'then' | 'now' | 'delta'): number | null {
  const pts = curve?.points ?? []
  if (pts.length < 2) return null
  const xs = pts.map((p) => num(p.shift) ?? 0)
  let i = xs.findIndex((x) => x >= shift)
  if (i === -1) i = pts.length - 1
  if (i === 0) i = 1
  const a = num(pts[i - 1][key])
  const b = num(pts[i][key])
  if (a === null || b === null) return null
  const t = (shift - xs[i - 1]) / (xs[i] - xs[i - 1] || 1)
  return a + (b - a) * Math.max(0, Math.min(1, t))
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
  marks = [],
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
  /** Points worth landing on — an expiry — drawn as ticks under the track. */
  marks?: { at: number; label: string }[]
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
        className="mt-2 h-1.5 w-full cursor-pointer appearance-none rounded-full bg-sunken accent-accent"
      />
      {marks.length > 0 && (
        <div className="relative mt-1 h-3">
          {marks.map((m) => (
            <button
              key={m.label}
              type="button"
              onClick={(e) => {
                e.preventDefault()
                onChange(m.at)
              }}
              title={m.label}
              className={`absolute top-0 h-3 w-1 -translate-x-1/2 rounded-full ${
                value >= m.at ? 'bg-loss' : 'bg-tested'
              }`}
              style={{ left: `${((m.at - min) / (max - min)) * 100}%` }}
            />
          ))}
        </div>
      )}
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
  children: ReactNode
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

/* P&L against price, for every price in the dial's range at once.

   Today's line stays underneath, dashed, so the gap to the scenario line is
   what volatility and time did. The dot is where the dial is. Drag anywhere
   on the chart to move it. */
function CurveChart({
  curve,
  shift,
  base,
  onShift,
  thenLabel = 'with your volatility and date',
}: {
  curve: ScenarioCurve
  shift: number
  base: number | null
  onShift: (s: number) => void
  thenLabel?: string
}) {
  const [measure, width] = useWidth<HTMLDivElement>()
  const dragging = useRef(false)
  const W = width ?? 640
  const H = 280
  const L = 64
  const R = W - 16
  const T = 18
  const B = H - 34

  const pts = curve.points
    .map((p) => ({ s: num(p.shift), then: num(p.then), now: num(p.now) }))
    .filter((p): p is { s: number; then: number | null; now: number | null } => p.s !== null)
  const values = pts.flatMap((p) => [p.then, p.now]).filter((v): v is number => v !== null)
  if (values.length === 0) {
    return (
      <div ref={measure} className="py-10 text-center text-[15px] text-muted">
        This position cannot be priced right now.
      </div>
    )
  }
  let lo = Math.min(0, ...values)
  let hi = Math.max(0, ...values)
  const pad = (hi - lo) * 0.08 || 1
  lo -= pad
  hi += pad

  const x = (s: number) => L + ((s + RANGE) / (2 * RANGE)) * (R - L)
  const y = (v: number) => T + ((hi - v) / (hi - lo)) * (B - T)
  const zero = y(0)

  const line = (key: 'then' | 'now') =>
    pts
      .filter((p) => p[key] !== null)
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.s).toFixed(1)},${y(p[key]!).toFixed(1)}`)
      .join(' ')
  const thenPts = pts.filter((p) => p.then !== null)
  const area =
    thenPts.length > 1
      ? `${line('then')} L${x(thenPts[thenPts.length - 1].s)},${zero} L${x(thenPts[0].s)},${zero} Z`
      : ''

  const here = at(curve, shift, 'then')
  const today = at(curve, 0, 'now')

  function pick(clientX: number, target: SVGSVGElement) {
    const box = target.getBoundingClientRect()
    const s = ((clientX - box.left - L) / (R - L)) * 2 * RANGE - RANGE
    const snapped = Math.round(Math.max(-RANGE, Math.min(RANGE, s)) / STEP) * STEP
    onShift(Number(snapped.toFixed(4)))
  }

  const ticks = [-0.2, -0.1, 0, 0.1, 0.2]
  const yTicks = [hi - pad, (hi + lo) / 2, lo + pad]
  const label = (s: number) => (base !== null ? level(base * (1 + s)) : pct(s, 0, true))

  return (
    <div ref={measure} className="select-none">
      {width !== null && (
        <svg
          width={W}
          height={H}
          className="block cursor-ew-resize touch-none"
          onPointerDown={(e) => {
            dragging.current = true
            e.currentTarget.setPointerCapture(e.pointerId)
            pick(e.clientX, e.currentTarget)
          }}
          onPointerMove={(e) => dragging.current && pick(e.clientX, e.currentTarget)}
          onPointerUp={() => (dragging.current = false)}
          role="img"
          aria-label="P&L against price, today and under the scenario"
        >
          <defs>
            <clipPath id="wi-above">
              <rect x={0} y={0} width={W} height={Math.max(zero, 0)} />
            </clipPath>
            <clipPath id="wi-below">
              <rect x={0} y={zero} width={W} height={Math.max(H - zero, 0)} />
            </clipPath>
          </defs>

          {/* Green where the scenario makes money, red where it loses. */}
          {area && (
            <>
              <path d={area} className="fill-profit/15" clipPath="url(#wi-above)" />
              <path d={area} className="fill-loss/15" clipPath="url(#wi-below)" />
            </>
          )}

          {yTicks.map((v, i) => (
            <text key={i} x={L - 8} y={y(v) + 4} textAnchor="end" className="fill-faint text-[12px]">
              {money(v, { sign: true, cents: false })}
            </text>
          ))}
          <line x1={L} x2={R} y1={zero} y2={zero} className="stroke-line-strong" />
          <text x={L - 8} y={zero + 4} textAnchor="end" className="fill-muted text-[12px]">
            $0
          </text>

          {ticks.map((s) => (
            <g key={s}>
              <line x1={x(s)} x2={x(s)} y1={B} y2={B + 4} className="stroke-line-strong" />
              <text x={x(s)} y={B + 18} textAnchor="middle" className="fill-faint text-[12px]">
                {label(s)}
              </text>
            </g>
          ))}

          {/* Today, underneath. */}
          <path d={line('now')} className="fill-none stroke-muted" strokeWidth={1.5} strokeDasharray="5 4" />
          {/* The scenario. */}
          <path d={line('then')} className="fill-none stroke-accent" strokeWidth={2.75} />

          {/* Where price is now, on today's line. */}
          {today !== null && (
            <circle cx={x(0)} cy={y(today)} r={4} className="fill-raised stroke-muted" strokeWidth={2} />
          )}

          {/* Where the dial is. */}
          <line x1={x(shift)} x2={x(shift)} y1={T} y2={B} className="stroke-accent/50" strokeDasharray="3 3" />
          {here !== null && (
            <>
              <circle cx={x(shift)} cy={y(here)} r={11} className="fill-accent/20" />
              <circle cx={x(shift)} cy={y(here)} r={6} className="fill-accent stroke-raised" strokeWidth={2} />
            </>
          )}
          <text
            x={Math.min(R - 40, Math.max(L + 40, x(shift)))}
            y={T - 4}
            textAnchor="middle"
            className="fill-accent text-[12px] font-semibold"
          >
            {label(shift)}
          </text>
        </svg>
      )}
      <div className="mt-1 flex flex-wrap gap-x-5 gap-y-1 pl-16 text-[13px] text-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5 bg-accent" /> {thenLabel}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0 w-5 border-t-2 border-dashed border-muted" /> today, nothing changed
        </span>
        <span className="text-faint">drag on the chart to move price</span>
      </div>
    </div>
  )
}

export function WhatIf() {
  // The positions under test; none picked means the whole book.
  const [selected, setSelected] = useState<string[]>([])
  const [book, setBook] = useState<ScenarioRow[]>([])
  const [price, setPrice] = useState(0)
  // Volatility as a VIX level; null until VIX is known, then VIX now.
  const [vixTo, setVixTo] = useState<number | null>(null)
  const [days, setDays] = useState(0)
  // Move SPY and let each product follow by its beta, or move the product itself.
  const [move, setMove] = useState<'spy' | 'underlying'>('spy')
  const [data, setData] = useState<ScenarioResult | null>(null)
  const [curve, setCurve] = useState<ScenarioCurve | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)

  const vixNow = num(data?.vix ?? null)
  const spyNow = num(data?.spy ?? null)
  const vixTarget = vixTo ?? vixNow
  // Every option's volatility moves by the proportion VIX moves by.
  const iv = vixNow && vixTarget ? vixTarget / vixNow - 1 : 0
  const byBeta = move === 'spy'

  // Which answer is the latest. Answers come back out of order — a whole
  // book is slower than one position — and an older one arriving last used
  // to overwrite the one for where the dials now are.
  const latestTable = useRef(0)
  const latestCurve = useRef(0)

  // The table: every position, repriced once a drag settles.
  useEffect(() => {
    const t = setTimeout(() => {
      const ticket = ++latestTable.current
      setBusy(true)
      api
        .scenario(price, iv, days, byBeta, selected)
        .then((d) => {
          if (ticket !== latestTable.current) return
          setData(d)
          if (selected.length === 0) setBook(d.positions)
          setError(null)
        })
        .catch((e) => setError(e instanceof Error ? e : new Error(String(e))))
        .finally(() => ticket === latestTable.current && setBusy(false))
    }, 120)
    return () => clearTimeout(t)
  }, [price, iv, days, byBeta, selected])

  // The curve: redrawn when volatility, time or the position change. Price
  // does not redraw it — price only moves the dot along it, instantly.
  useEffect(() => {
    const t = setTimeout(() => {
      const ticket = ++latestCurve.current
      api
        .scenarioCurve(iv, days, byBeta, selected)
        .then((c) => ticket === latestCurve.current && setCurve(c))
        .catch(() => undefined)
    }, 60)
    return () => clearTimeout(t)
  }, [iv, days, byBeta, selected])

  // Straight off the curve, so the big number moves while the dial does.
  const then = at(curve, price, 'then')
  const nowLive = at(curve, 0, 'now')
  const change = then !== null && nowLive !== null ? then - nowLive : null
  const deltaThen = at(curve, price, 'delta')
  const shownThen = useTween(then)
  const shownChange = useTween(change)

  if (error && !data) return <ErrorPanel error={error} onRetry={() => setPrice((p) => p)} />
  if (!data) return <Loading label="Pricing your book" />

  const untouched = price === 0 && Math.abs(iv) < 1e-9 && days === 0
  // Every expiry among the positions in view, with how many legs expire
  // then and how many days away it is. The clock dial reaches the furthest.
  const expiryMap = new Map<string, number>()
  for (const r of data.positions) {
    for (const l of r.legs) {
      if (l.expiration) expiryMap.set(l.expiration, (expiryMap.get(l.expiration) ?? 0) + 1)
    }
  }
  const expiries = [...expiryMap.entries()]
    .map(([date, legs]) => ({ date, legs, days: daysUntil(date) }))
    .filter((x) => x.days >= 0)
    .sort((a, b) => a.days - b.days)
  const daysMax = Math.min(400, Math.max(60, ...expiries.map((x) => x.days)))
  const expired = expiries.filter((x) => days >= x.days)
  const picked = book.filter((r) => selected.includes(r.id))
  // Several positions on one product — /ZBZ6 and /ZBH7 are both bonds — get
  // everything a single position does: the product's own price on the dial,
  // a delta that adds up, and every leg in one table.
  const roots = new Set(picked.map((r) => productOf(r.underlying)))
  const oneProduct = picked.length > 0 && roots.size === 1
  const product = oneProduct ? (picked.length === 1 ? picked[0].underlying : [...roots][0]) : null
  const base = move === 'spy' ? spyNow : oneProduct ? num(picked[0].price) : null
  const priceLabel = move === 'spy' ? 'SPY' : (product ?? 'Every product')
  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
  const vixMax = Math.max(60, Math.ceil((vixNow ?? 20) * 2))
  const maxChange = Math.max(1, ...data.positions.map((r) => Math.abs(num(r.change) ?? 0)))

  const controls = (
    <section className="sheened rounded-card border border-line bg-raised p-5 shadow-[var(--shadow-sm)]">
      <div className="mb-4 flex flex-wrap items-center gap-2 text-[14px]">
        <span className="text-muted">Move</span>
        <Choice on={move === 'spy'} onClick={() => setMove('spy')}>
          SPY{spyNow !== null ? ` ${level(spyNow)}` : ''}
        </Choice>
        <Choice on={move === 'underlying'} onClick={() => setMove('underlying')}>
          {product ?? 'each product itself'}
        </Choice>
      </div>

      <div className="space-y-6">
        <Dial
          label={priceLabel}
          value={price}
          min={-RANGE}
          max={RANGE}
          step={STEP}
          show={base !== null ? level(base * (1 + price)) : pct(price, 1, true)}
          sub={base !== null ? pct(price, 1, true) : undefined}
          ends={
            base !== null
              ? [level(base * (1 - RANGE)), level(base * (1 + RANGE))]
              : [pct(-RANGE, 0, true), pct(RANGE, 0, true)]
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
            sub={Math.abs(iv) < 1e-9 ? 'now' : `${iv > 0 ? '+' : ''}${(iv * 100).toFixed(0)}% vol`}
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
          max={daysMax}
          step={1}
          show={days === 0 ? 'today' : `+${days}d`}
          sub={days === 0 ? undefined : shortDate(dateIn(days))}
          ends={['today', `${daysMax} days`]}
          onChange={setDays}
          marks={expiries.map((x) => ({ at: x.days, label: `${shortDate(x.date)} expiry · ${x.legs} leg${x.legs === 1 ? '' : 's'}` }))}
        />
        {/* Every expiry among these positions: one click jumps the clock
            there, and the ones already passed are marked as settled. */}
        {expiries.length > 0 && (
          <div className="-mt-3 flex flex-wrap gap-1.5">
            {expiries.map((x) => {
              const gone = days >= x.days
              return (
                <button
                  key={x.date}
                  onClick={() => setDays(x.days)}
                  className={`rounded-sm border px-2 py-0.5 text-[12px] transition-colors ${
                    gone
                      ? 'border-loss/40 bg-loss-soft text-loss'
                      : 'border-tested/40 bg-tested-soft text-tested hover:border-tested'
                  }`}
                  title={gone ? 'Expired by then — settled at intrinsic' : 'Jump to this expiry'}
                >
                  {shortDate(x.date)} · {x.legs} leg{x.legs === 1 ? '' : 's'}
                  {gone ? ' · expired' : ''}
                </button>
              )
            })}
          </div>
        )}
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
            className="rounded-sm border border-line px-2.5 py-1 text-[13px] text-muted transition-colors hover:bg-hover hover:text-ink"
          >
            {p.label}
          </button>
        ))}
      </div>
      <button
        onClick={() => {
          setPrice(0)
          setVixTo(null)
          setDays(0)
        }}
        disabled={untouched}
        className="mt-3 w-full rounded-sm border border-accent/50 bg-accent-soft px-3 py-1.5 text-[14px] text-accent transition-opacity disabled:opacity-30"
      >
        Back to now
      </button>

      <p className="mt-4 text-[13px] leading-relaxed text-muted">
        {move === 'spy'
          ? 'SPY moves and each product follows by its own beta.'
          : product !== null
            ? `${product} moves directly.`
            : 'Each product moves by the same percentage of its own price.'}{' '}
        VIX sets volatility: every option’s volatility moves by the same proportion. Days forward
        runs the clock; a leg that expires on the way settles at intrinsic.
      </p>
    </section>
  )

  return (
    <div className="space-y-5">
      <SectionHeading
        title="What if"
        hint="drag a dial or the chart — everything moves with it"
      />

      {/* Which positions. The whole book, or any set of trades — pick as
          many as you like and they are tested together. */}
      <div className="flex flex-wrap items-center gap-2">
        <Choice on={selected.length === 0} onClick={() => setSelected([])}>
          Whole book
        </Choice>
        {book.map((r) => (
          <Choice key={r.id} on={selected.includes(r.id)} onClick={() => toggle(r.id)}>
            <span className="font-medium">{r.underlying}</span>{' '}
            <span className="text-[13px] opacity-80">{r.name}</span>
          </Choice>
        ))}
      </div>

      <div className="grid items-start gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
        {/* Pinned, so the dials never scroll away from what they move. */}
        <aside className="order-2 xl:sticky xl:top-24 xl:order-1">{controls}</aside>

        <div className="order-1 space-y-5 xl:order-2">
          <section className="sheened rounded-card border border-line bg-raised p-5 shadow-[var(--shadow-sm)]">
            {/* The answer, large, moving as the dials do. */}
            <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
              <div>
                <div className="text-[12px] uppercase tracking-wider text-muted">
                  {picked.length === 0
                    ? 'Your book would be'
                    : picked.length === 1
                      ? `${picked[0].underlying} would be`
                      : `These ${picked.length} would be`}
                </div>
                <div className={`figure text-[44px] font-semibold leading-none ${signedClass(shownThen)}`}>
                  {shownThen === null ? '—' : money(shownThen, { sign: true, cents: false })}
                </div>
              </div>
              <div>
                <div className="text-[12px] uppercase tracking-wider text-muted">Change</div>
                <div className={`figure text-[28px] font-semibold leading-none ${signedClass(shownChange)}`}>
                  {shownChange === null ? '—' : money(shownChange, { sign: true, cents: false })}
                </div>
              </div>
              <div>
                <div className="text-[12px] uppercase tracking-wider text-muted">Today</div>
                <div className={`figure text-[20px] leading-none ${signedClass(nowLive)}`}>
                  {nowLive === null ? '—' : money(nowLive, { sign: true, cents: false })}
                </div>
              </div>
              {deltaThen !== null && (
                <div>
                  <div className="text-[12px] uppercase tracking-wider text-muted">Delta then</div>
                  <div className="figure text-[20px] leading-none">
                    {deltaThen > 0 ? '+' : ''}
                    {deltaThen.toFixed(2)}
                  </div>
                </div>
              )}
              {busy && <div className="ml-auto text-[13px] text-faint">repricing…</div>}
            </div>
            {expired.length > 0 && (
              <p className="mt-2 text-[14px] text-loss">
                By {shortDate(dateIn(days))},{' '}
                {expired.reduce((a, x) => a + x.legs, 0)} leg
                {expired.reduce((a, x) => a + x.legs, 0) === 1 ? ' has' : 's have'} expired (
                {expired.map((x) => shortDate(x.date)).join(', ')}) — counted at what they are
                worth at expiry, so the curve for them is the payoff, not a price.
              </p>
            )}

            <div className="mt-4">
              {curve ? (
                <CurveChart
                  curve={curve}
                  shift={price}
                  base={base}
                  onShift={setPrice}
                  thenLabel={
                    days === 0
                      ? 'with your volatility, today'
                      : `on ${shortDate(dateIn(days))}` +
                        (expired.length > 0
                          ? `, after ${expired.reduce((a, x) => a + x.legs, 0)} leg(s) expired`
                          : '')
                  }
                />
              ) : (
                <div className="py-16 text-center text-[14px] text-faint">Drawing the curve…</div>
              )}
            </div>
          </section>

          {data.unpriced > 0 && (
            <p className="text-[14px] text-warn">
              {data.unpriced} position{data.unpriced === 1 ? '' : 's'} could not be priced — a leg
              with no mark and no implied volatility — so the totals are left blank rather than
              shown short.
            </p>
          )}

          {/* Every position, with a bar for how much the scenario moves it. */}
          <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
            <table className="w-full text-[16px]">
              <thead>
                <tr className="border-b border-line text-left text-[13px] uppercase tracking-wider text-muted">
                  <th className="py-3 pl-4 pr-5 font-medium">Position</th>
                  <th className="py-3 pr-5 text-right font-medium">Price then</th>
                  <th className="py-3 pr-5 text-right font-medium">Delta</th>
                  <th className="py-3 pr-5 text-right font-medium">P&L then</th>
                  <th className="py-3 pr-4 font-medium">Change</th>
                </tr>
              </thead>
              <tbody className="num">
                {data.positions.map((r) => {
                  const c = num(r.change) ?? 0
                  return (
                    <tr
                      key={r.id}
                      onClick={() => toggle(r.id)}
                      className={`cursor-pointer border-b border-line/60 transition-colors last:border-0 hover:bg-hover ${
                        selected.includes(r.id) ? 'bg-accent-soft' : ''
                      }`}
                    >
                      <td className="py-2.5 pl-4 pr-5">
                        <div className="font-medium">{r.underlying}</div>
                        <div className="text-[13px] text-muted">{r.name}</div>
                      </td>
                      <td className="whitespace-nowrap py-2.5 pr-5 text-right">
                        {num(r.price_then) === null ? '—' : level(num(r.price_then)!)}
                      </td>
                      <td className="whitespace-nowrap py-2.5 pr-5 text-right">
                        <DeltaMove from={r.delta_now} to={r.delta_then} moved={!untouched} />
                      </td>
                      <td className={`whitespace-nowrap py-2.5 pr-5 text-right font-medium ${signedClass(r.then)}`}>
                        {money(r.then, { sign: true, cents: false })}
                      </td>
                      <td className="w-[38%] py-2.5 pr-4">
                        <div className="flex items-center gap-3">
                          <div className="relative h-2.5 flex-1 rounded-full bg-sunken">
                            <div
                              className={`absolute inset-y-0 rounded-full transition-all duration-300 ${
                                c >= 0 ? 'left-1/2 bg-profit' : 'right-1/2 bg-loss'
                              }`}
                              style={{ width: `${(Math.abs(c) / maxChange) * 50}%` }}
                            />
                            <div className="absolute inset-y-[-3px] left-1/2 w-px bg-line-strong" />
                          </div>
                          <span className={`w-20 text-right font-semibold ${signedClass(r.change)}`}>
                            {money(r.change, { sign: true, cents: false })}
                          </span>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* One position: each leg on its own — what it is worth, what it
              has made, and where the scenario takes both — because the
              position's number is the sum of these and it is here that you
              see which leg is doing the work. Delta changes by strike too: a
              strike far from the price barely moves with it; the same strike
              once price reaches it moves half as much as the underlying. */}
          {oneProduct && data.positions.some((r) => r.legs.length > 0) && (
            <LegTable
              legs={data.positions.flatMap((r) =>
                r.legs.map((l) => ({ ...l, owner: data.positions.length > 1 ? `${r.underlying} ${r.name}` : null })),
              )}
              moved={!untouched}
              priceMoved={price !== 0}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function LegTable({
  legs,
  moved,
  priceMoved,
}: {
  legs: (ScenarioRow['legs'][number] & { owner?: string | null })[]
  moved: boolean
  priceMoved: boolean
}) {
  const sum = (key: 'pnl_now' | 'pnl_then' | 'change' | 'position_delta_now' | 'position_delta_then') => {
    let total = 0
    for (const l of legs) {
      const v = num(l[key])
      if (v === null) return null
      total += v
    }
    return total
  }
  const maxChange = Math.max(1, ...legs.map((l) => Math.abs(num(l.change) ?? 0)))
  const head = 'py-3 pr-5 text-right font-medium'
  const cell = 'whitespace-nowrap py-3 pr-5 text-right'

  return (
    <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
      <div className="px-4 pt-4 text-[13px] font-semibold uppercase tracking-wider text-muted">
        Each leg — what it is worth, what it has made, and where the scenario takes it
      </div>
      <table className="w-full text-[16px]">
        <thead>
          <tr className="border-b border-line text-left text-[13px] uppercase tracking-wider text-muted">
            <th className="py-3 pl-4 pr-5 font-medium">Leg</th>
            <th className={head}>Entry</th>
            <th className={head}>Price now → then</th>
            <th className={head}>Leg P&L now → then</th>
            <th className="py-3 pr-5 font-medium">Change</th>
            <th className={head}>Price vs strike</th>
            <th className={head}>Delta</th>
            <th className="py-3 pr-4 text-right font-medium">Position Δ</th>
          </tr>
          {/* The legs added up: the position's own numbers, at the top. */}
          <tr className="border-b-2 border-line-strong text-[15px]">
            <td className="py-2.5 pl-4 pr-5 text-[13px] font-semibold uppercase tracking-wider text-muted">
              All {legs.length} legs
            </td>
            <td />
            <td />
            <td className={cell}>
              <Move from={sum('pnl_now')} to={sum('pnl_then')} moved={moved} money />
            </td>
            <td className="py-2.5 pr-5">
              <span className={`num font-semibold ${signedClass(sum('change'))}`}>
                {money(sum('change'), { sign: true, cents: false })}
              </span>
            </td>
            <td />
            <td />
            <td className="whitespace-nowrap py-2.5 pr-4 text-right">
              <Move from={sum('position_delta_now')} to={sum('position_delta_then')} moved={moved} />
            </td>
          </tr>
        </thead>
        <tbody className="num">
          {legs.map((l, i) => {
            const k = num(l.strike)
            const a = num(l.underlying_now)
            const b = num(l.underlying_then)
            const c = num(l.change) ?? 0
            const away = (s: number | null) =>
              k === null || s === null ? '—' : `${s - k >= 0 ? '+' : ''}${level(s - k)}`
            return (
              <tr key={i} className="border-b border-line/60 last:border-0">
                <td className="whitespace-nowrap py-3 pl-4 pr-5">
                  <span
                    className={`mr-2 inline-block w-12 rounded px-1 py-0.5 text-center text-[12px] uppercase ${
                      l.side === 'short' ? 'bg-accent-soft text-accent' : 'bg-sunken text-muted'
                    }`}
                  >
                    {l.side}
                  </span>
                  {Number(l.quantity)} ×{' '}
                  {k === null ? l.right : `${level(k)} ${l.right === 'C' ? 'call' : 'put'}`}
                  {l.owner && <div className="mt-0.5 text-[12px] text-muted">{l.owner}</div>}
                </td>
                <td className={`${cell} text-muted`}>{level(num(l.open_price) ?? 0)}</td>
                <td className={cell}>
                  <Move from={num(l.price_now)} to={num(l.price_then)} moved={moved} plain />
                </td>
                <td className={cell}>
                  <Move from={num(l.pnl_now)} to={num(l.pnl_then)} moved={moved} money />
                </td>
                <td className="py-3 pr-5">
                  <div className="flex items-center gap-2">
                    <div className="relative h-2 w-24 rounded-full bg-sunken">
                      <div
                        className={`absolute inset-y-0 rounded-full transition-all duration-300 ${
                          c >= 0 ? 'left-1/2 bg-profit' : 'right-1/2 bg-loss'
                        }`}
                        style={{ width: `${(Math.abs(c) / maxChange) * 50}%` }}
                      />
                      <div className="absolute inset-y-[-2px] left-1/2 w-px bg-line-strong" />
                    </div>
                    <span className={`w-20 text-right font-semibold ${signedClass(l.change)}`}>
                      {money(l.change, { sign: true, cents: false })}
                    </span>
                  </div>
                </td>
                <td className={`${cell} text-muted`}>
                  {k === null ? (
                    '—'
                  ) : (
                    <>
                      {away(a)}
                      {priceMoved && (
                        <>
                          <span className="text-faint"> → </span>
                          <span className="text-ink">{away(b)}</span>
                        </>
                      )}
                    </>
                  )}
                </td>
                <td className={cell}>
                  <DeltaMove from={l.delta_now} to={l.delta_then} moved={moved} plain />
                </td>
                <td className="whitespace-nowrap py-3 pr-4 text-right">
                  <DeltaMove from={l.position_delta_now} to={l.position_delta_then} moved={moved} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/* A number and where the scenario takes it, as money, a price or a delta. */
function Move({
  from,
  to,
  moved,
  money: asMoney = false,
  plain = false,
}: {
  from: number | null
  to: number | null
  moved: boolean
  money?: boolean
  plain?: boolean
}) {
  if (from === null) return <span className="text-faint">—</span>
  const show = (n: number) =>
    asMoney ? money(n, { sign: true, cents: false }) : plain ? level(n) : `${n > 0 ? '+' : ''}${n.toFixed(2)}`
  const tone = (n: number) => (plain ? 'text-ink' : signedClass(n))
  const changed = moved && to !== null && Math.abs(to - from) >= (asMoney ? 0.5 : 0.005)
  return (
    <span>
      <span className={changed ? 'text-muted' : tone(from)}>{show(from)}</span>
      {changed && (
        <>
          <span className="text-faint"> → </span>
          <span className={`font-semibold ${tone(to!)}`}>{show(to!)}</span>
        </>
      )}
    </span>
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
