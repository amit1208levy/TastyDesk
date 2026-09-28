import { num, shortDate, strike } from '../lib/format'
import { useWidth } from '../lib/useMeasure'
import type { StrategyView } from '../types'

/* Where price can go by expiry, and what that means for this position.

   The first version drew a blue band and some dashed lines and left the
   reader to work out which side of which line was good. He could not, and
   should not have had to. So the line itself now says it: green where you
   keep the premium, red past your short strikes. Above it, a bracket shows
   where the market expects price to finish. Below it, where price is now.
   The question "am I safe?" becomes "does the bracket reach the red?" — and
   the sentence underneath answers it in words, with the distances in points
   and percent. */

// Prices this app shows span 2,830 (/RTY) and 103.16 (/ZB). Two decimals on
// the first is noise; none on the second loses the point.
// Decided by the underlying, not the number: a 330-point gap on /RTY is
// "330", not "330.00".
function price(n: number, scale: number = n): string {
  const digits = Math.abs(scale) >= 1000 ? 0 : 2
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function percent(n: number): string {
  return `${(n * 100).toFixed(1)}%`
}

export function ExpectedRange({ view }: { view: StrategyView }) {
  const [measure, width] = useWidth<HTMLDivElement>()
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

  // The strikes that bound the safe zone: the highest short put and the
  // lowest short call. Beyond either is where losses start.
  const puts = shorts.filter((s) => s.right === 'P')
  const calls = shorts.filter((s) => s.right === 'C')
  const put = puts.length ? puts[puts.length - 1] : null
  const call = calls.length ? calls[0] : null

  const inside = shorts.filter((s) => s.price >= low && s.price <= high)
  const expiry = view.strategy.legs.find((l) => l.expiration)?.expiration ?? null
  const by = expiry ? shortDate(expiry) : 'expiry'

  const points = [low, high, spot, ...shorts.map((s) => s.price)]
  const min = Math.min(...points)
  const max = Math.max(...points)
  const pad = (max - min) * 0.08 || 1
  const from = min - pad
  const to = max + pad

  // Drawn at the width it is shown at, so 13px text stays 13px.
  const W = width ?? 800
  const L = 8
  const R = W - 8
  const x = (p: number) => L + ((p - from) / (to - from)) * (R - L)
  const clamp = (v: number, half = 40) => Math.min(R - half, Math.max(L + half, v))

  const barTop = 50
  const barH = 26
  const barMid = barTop + barH / 2
  const below = barTop + barH + 20

  const safeFrom = put ? x(put.price) : L
  const safeTo = call ? x(call.price) : R

  // Strike labels sit under the bar beside "now"; one that would overlap it
  // drops a line rather than printing on top of it.
  const labelRow = (at: number) => (Math.abs(at - x(spot)) < 90 ? below + 20 : below)
  const H = below + 30

  const safe = inside.length === 0

  const distance = (s: { price: number; right: 'C' | 'P' }) => {
    const gap = s.right === 'P' ? spot - s.price : s.price - spot
    const name = `${strike(String(s.price))} ${s.right === 'C' ? 'call' : 'put'}`
    if (gap <= 0) return `is already past your ${name}, by ${price(-gap, spot)}`
    return `has to ${s.right === 'P' ? 'fall' : 'rise'} ${price(gap, spot)} (${percent(gap / spot)}) to reach your ${name}`
  }
  const needs = [put, call].filter((s): s is NonNullable<typeof s> => s !== null).map(distance)

  const middle = (safeFrom + safeTo) / 2
  const keepAt = Math.abs(middle - x(spot)) < 90 ? middle + 110 : middle

  return (
    <div ref={measure} className="mt-4 border-t border-line pt-4">
      <div className="text-[14px] font-medium uppercase tracking-wider text-muted">
        Where it can get to by {by}
      </div>

      {/* The answer first, in words. */}
      <p className={`mt-2 text-[17px] font-medium ${safe ? 'text-profit' : 'text-tested'}`}>
        {shorts.length === 0
          ? 'You are short nothing here, so no strike can be reached.'
          : safe
            ? 'Safe by the market’s own numbers — an ordinary move does not reach your strikes.'
            : `Tested — an ordinary move reaches your ${strike(String(inside[0].price))} ${
                inside[0].right === 'C' ? 'call' : 'put'
              }.`}
      </p>

      {width !== null && (
        <svg
          width={W}
          height={H}
          className="mt-2 block"
          role="img"
          aria-label={`Price is ${price(spot)}. The market expects it between ${price(low)} and ${price(high)} by ${by}.`}
        >
          {/* The likely range, as a bracket over the line. */}
          <text x={clamp(x(low), 30)} y={16} textAnchor="middle" className="fill-accent text-[13px]">
            {price(low)}
          </text>
          <text x={clamp(x(high), 30)} y={16} textAnchor="middle" className="fill-accent text-[13px]">
            {price(high)}
          </text>
          {x(high) - x(low) > 300 && (
            <text x={(x(low) + x(high)) / 2} y={16} textAnchor="middle" className="fill-accent text-[13px]">
              likely range · 2 in 3 chance
            </text>
          )}
          <path
            d={`M${x(low)},${barTop - 6} V${barTop - 22} H${x(high)} V${barTop - 6}`}
            className="fill-none stroke-accent"
            strokeWidth={2}
          />

          {/* The line, coloured by what happens to you if price ends there. */}
          <rect x={L} y={barTop} width={R - L} height={barH} rx={5} className="fill-loss-soft" />
          <rect
            x={safeFrom}
            y={barTop}
            width={Math.max(safeTo - safeFrom, 0)}
            height={barH}
            className="fill-profit-soft"
          />
          {safeTo - safeFrom > 170 && shorts.length > 0 && (
            <text x={keepAt} y={barMid + 4} textAnchor="middle" className="fill-profit text-[12px] font-medium">
              you keep the premium
            </text>
          )}
          {put && safeFrom - L > 70 && (
            <text x={(L + safeFrom) / 2} y={barMid + 4} textAnchor="middle" className="fill-loss text-[12px] font-medium">
              losing
            </text>
          )}
          {call && R - safeTo > 70 && (
            <text x={(safeTo + R) / 2} y={barMid + 4} textAnchor="middle" className="fill-loss text-[12px] font-medium">
              losing
            </text>
          )}

          {/* Your short strikes: where green turns red. */}
          {shorts.map((s) => {
            const hit = s.price >= low && s.price <= high
            const at = x(s.price)
            return (
              <g key={`${s.right}${s.price}`}>
                <line
                  x1={at}
                  x2={at}
                  y1={barTop - 3}
                  y2={barTop + barH + 3}
                  className={hit ? 'stroke-tested' : 'stroke-ink'}
                  strokeWidth={2}
                />
                <text
                  x={clamp(at)}
                  y={labelRow(at)}
                  textAnchor="middle"
                  className={`text-[13px] ${hit ? 'fill-tested font-medium' : 'fill-ink'}`}
                >
                  your {strike(String(s.price))} {s.right === 'C' ? 'call' : 'put'}
                </text>
              </g>
            )
          })}

          {/* Now. */}
          <line
            x1={x(spot)}
            x2={x(spot)}
            y1={barTop - 4}
            y2={barTop + barH + 4}
            className="stroke-accent"
            strokeWidth={3}
          />
          <circle cx={x(spot)} cy={barMid} r={5} className="fill-accent" />
          <text x={clamp(x(spot))} y={below} textAnchor="middle" className="fill-accent text-[13px] font-medium">
            now {price(spot)}
          </text>
        </svg>
      )}

      {/* The distances, in words and in the underlying's own units. */}
      <p className="mt-2 text-[15px] leading-relaxed text-ink">
        {needs.length > 0 && <>Price {needs.join(', or ')}. </>}
        The market expects a move of about ±{price(move, spot)} by {by}.
      </p>
      <p className="mt-1 text-[14px] text-muted">
        The blue bracket is one expected move either way — the market’s own number, not a
        forecast. Price ends inside it about two times in three.
      </p>
    </div>
  )
}
