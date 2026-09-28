import { decimals, num, shortDate, strike } from '../lib/format'
import type { StrategyView } from '../types'

/* Where the market thinks this thing can get to, drawn against your strikes.

   The panel said the underlying was "21.9% below your 113 short call" and that
   the market prices "a move of about 21.87 by expiry", and expected the reader
   to do the arithmetic and believe the conclusion. He did the arithmetic,
   compared a percentage with a price, and quite reasonably could not see the
   danger.

   A distance is hard to picture. A place is not. So this draws the range the
   market is pricing — 70.85 to 114.59 by the 17th — and puts the short strikes
   on the same line. A strike inside the band needs no explaining: the market
   is already pricing a move that reaches it. A strike outside it needs none
   either.

   The band is one expected move either way, which is roughly a two-in-three
   chance of finishing inside it. That is stated rather than implied, because
   "the expected range" sounds like a promise and is not one. */
export function ExpectedRange({ view, wide = false }: { view: StrategyView; wide?: boolean }) {
  const spot = num(view.underlying_price)
  const move = num(view.risk.expected_move)
  if (spot === null || move === null || move <= 0) return null

  const low = spot - move
  const high = spot + move

  const shorts = view.strategy.legs
    .filter((l) => l.direction === 'Short' && l.strike !== null && l.option_type)
    .map((l) => ({ price: num(l.strike)!, right: l.option_type as 'C' | 'P' }))
    .filter((s) => Number.isFinite(s.price))
    .sort((a, b) => a.price - b.price)

  const points = [low, high, spot, ...shorts.map((s) => s.price)]
  const min = Math.min(...points)
  const max = Math.max(...points)
  const pad = (max - min) * 0.1 || 1
  const from = min - pad
  const to = max + pad

  // Narrow on purpose: this sits in the right-hand column, and a wide viewBox
  // scaled down to fit it shrinks every label with it. Three rows, so nothing
  // has to share a line with anything it could collide with — strikes above,
  // the bar, then where the range starts and ends.
  // Laid out wide, across the risk panel, the same drawing gets a wider
  // canvas rather than being blown up: text stays the size of the text
  // around it, and the strikes get room to spread out.
  const W = wide ? 900 : 280
  const H = 92
  const L = 4
  const R = W - 4
  const axis = 46
  const x = (p: number) => L + ((p - from) / (to - from)) * (R - L)
  const clamp = (v: number) => Math.min(R - 22, Math.max(L + 22, v))

  const inside = shorts.filter((s) => s.price >= low && s.price <= high)
  const expiry = view.strategy.legs.find((l) => l.expiration)?.expiration ?? null

  return (
    <div
      className={wide ? 'mt-4 border-t border-line pt-4' : 'mt-3.5 border-t border-line pt-3.5'}
    >
      <div className="text-[14px] font-medium uppercase tracking-wider text-muted">
        Where it can get to by expiry
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className={`mt-1 w-full ${wide ? 'max-w-[900px]' : ''}`} role="img" aria-label={
        `The market prices ${view.strategy.underlying} between ${decimals(low, 2)} and ${decimals(high, 2)} by expiry. ` +
        (shorts.length === 0
          ? 'This position has no short strikes.'
          : `${inside.length} of your ${shorts.length} short strikes are inside that range.`)
      }>
        <rect
          x={x(low)}
          y={axis - 13}
          width={x(high) - x(low)}
          height={26}
          rx={4}
          className="fill-accent/15 stroke-accent/40"
        />
        <line x1={L} x2={R} y1={axis} y2={axis} className="stroke-line-strong" />

        {/* Now, in the middle of its own range by construction. */}
        <line
          x1={x(spot)}
          x2={x(spot)}
          y1={axis - 15}
          y2={axis + 15}
          className="stroke-accent"
          strokeWidth={2}
        />

        {/* The strikes you are short, on the line the range is drawn on,
            because the only question is whether the range reaches them. */}
        {shorts.map((s) => {
          const hit = s.price >= low && s.price <= high
          return (
            <g key={`${s.right}${s.price}`}>
              <line
                x1={x(s.price)}
                x2={x(s.price)}
                y1={axis - 13}
                y2={axis + 13}
                className={hit ? 'stroke-tested' : 'stroke-profit'}
                strokeWidth={2}
                strokeDasharray={hit ? undefined : '3 3'}
              />
              <text
                x={clamp(x(s.price))}
                y={axis - 19}
                textAnchor="middle"
                className={`text-[13px] ${hit ? 'fill-tested' : 'fill-profit'}`}
              >
                {strike(String(s.price))} {s.right === 'C' ? 'call' : 'put'}
              </text>
            </g>
          )
        })}

        <text x={clamp(x(low))} y={axis + 27} textAnchor="middle" className="fill-muted text-[13px]">
          {decimals(low, 2)}
        </text>
        <text x={x(spot)} y={axis + 27} textAnchor="middle" className="fill-accent text-[13px]">
          now {decimals(spot, 2)}
        </text>
        <text x={clamp(x(high))} y={axis + 27} textAnchor="middle" className="fill-muted text-[13px]">
          {decimals(high, 2)}
        </text>
      </svg>

      <p className="mt-1 text-[15px] leading-relaxed text-ink">
        By {expiry ? shortDate(expiry) : 'expiry'} the market is pricing{' '}
        {view.strategy.underlying} anywhere between{' '}
        <span className="figure">{decimals(low, 2)}</span> and{' '}
        <span className="figure">{decimals(high, 2)}</span>.{' '}
        {shorts.length === 0 ? (
          'You are short nothing here.'
        ) : inside.length === 0 ? (
          <span className="text-profit">
            Every strike you are short is outside that — the market is not pricing a move that
            reaches you.
          </span>
        ) : (
          <span className="text-tested">
            {inside.length === shorts.length && shorts.length > 1
              ? 'Every strike you are short is inside that'
              : `Your ${strike(String(inside[0].price))} short ${inside[0].right === 'C' ? 'call' : 'put'} is inside that`}
            , which is all "tested" means: an ordinary move gets there.
          </span>
        )}
      </p>
      <p className="mt-1 text-[14px] text-muted">
        One expected move either way — the market's own number, not a forecast. Price finishes
        inside a range like this about two times in three.
      </p>
    </div>
  )
}
