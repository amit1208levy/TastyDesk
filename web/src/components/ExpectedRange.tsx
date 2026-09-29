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

  // Every option leg, long and short: a long put is protection, and leaving
  // it off drew a Bull ZB with three puts as if it had one.
  const strikes = view.strategy.legs
    .filter((l) => l.strike !== null && l.option_type)
    .map((l) => ({
      price: num(l.strike)!,
      right: l.option_type as 'C' | 'P',
      short: l.direction === 'Short',
      qty: num(l.quantity) ?? 1,
    }))
    .filter((s) => Number.isFinite(s.price))
    .sort((a, b) => a.price - b.price)
  const shorts = strikes.filter((s) => s.short)

  // What every leg is worth at expiry if price ends at p, added up — futures
  // and shares included. This, not the short strikes on their own, is what
  // decides where the line is green and where it is red.
  const payoff = (p: number): number => {
    let total = 0
    for (const l of view.strategy.legs) {
      const q = (num(l.quantity) ?? 0) * (l.direction === 'Short' ? -1 : 1) * (num(l.multiplier) ?? 1)
      const open = num(l.open_price) ?? 0
      const k = num(l.strike)
      if (l.option_type && k !== null) {
        const intrinsic = l.option_type === 'C' ? Math.max(p - k, 0) : Math.max(k - p, 0)
        total += (intrinsic - open) * q
      } else {
        total += (p - open) * q
      }
    }
    return total
  }

  // The strikes the price has to reach to hurt: the highest short put and
  // the lowest short call.
  const puts = shorts.filter((s) => s.right === 'P')
  const calls = shorts.filter((s) => s.right === 'C')
  const put = puts.length ? puts[puts.length - 1] : null
  const call = calls.length ? calls[0] : null

  const inside = shorts.filter((s) => s.price >= low && s.price <= high)
  const expiry = view.strategy.legs.find((l) => l.expiration)?.expiration ?? null
  const by = expiry ? shortDate(expiry) : 'expiry'

  const points = [low, high, spot, ...strikes.map((s) => s.price)]
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

  // Green and red runs along the line, from the payoff at each pixel.
  const runs: { from: number; to: number; win: boolean }[] = []
  for (let px = L; px <= R; px += 2) {
    const p = from + ((px - L) / (R - L)) * (to - from)
    const win = payoff(p) >= 0
    const last = runs[runs.length - 1]
    if (last && last.win === win) last.to = px + 2
    else runs.push({ from: px, to: px + 2, win })
  }
  if (runs.length) runs[runs.length - 1].to = R

  // Labels under the bar, placed left to right; one that would overlap the
  // one before drops to the next line instead of printing on top of it.
  type Tag = { at: number; text: string; cls: string }
  const tags: Tag[] = [
    ...strikes.map((s) => {
      const hit = s.short && s.price >= low && s.price <= high
      const name = `${strike(String(s.price))} ${s.right === 'C' ? 'call' : 'put'}${s.qty > 1 ? ` ×${s.qty}` : ''}`
      return {
        at: x(s.price),
        text: s.short ? `short ${name}` : `long ${name}`,
        cls: hit ? 'fill-tested font-medium' : s.short ? 'fill-ink' : 'fill-muted',
      }
    }),
    { at: x(spot), text: `now ${price(spot)}`, cls: 'fill-accent font-medium' },
  ].sort((a, b) => a.at - b.at)
  const rowEnds: number[] = []
  const placed = tags.map((tag) => {
    const half = tag.text.length * 3.6 + 6
    const cx = clamp(tag.at, half)
    let row = rowEnds.findIndex((end) => cx - half > end)
    if (row === -1) {
      row = rowEnds.length
      rowEnds.push(0)
    }
    rowEnds[row] = cx + half
    return { ...tag, cx, row }
  })
  const H = below + 18 * Math.max(rowEnds.length, 1) + 8

  const safe = inside.length === 0

  const distance = (s: { price: number; right: 'C' | 'P' }) => {
    const gap = s.right === 'P' ? spot - s.price : s.price - spot
    const name = `${strike(String(s.price))} ${s.right === 'C' ? 'call' : 'put'}`
    if (gap <= 0) return `is already past your ${name}, by ${price(-gap, spot)}`
    return `has to ${s.right === 'P' ? 'fall' : 'rise'} ${price(gap, spot)} (${percent(gap / spot)}) to reach your ${name}`
  }
  const needs = [put, call].filter((s): s is NonNullable<typeof s> => s !== null).map(distance)


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

          {/* The line, coloured by what the whole position makes or loses if
              price ends there at expiry. */}
          <clipPath id={`er-${view.strategy.id}`}>
            <rect x={L} y={barTop} width={R - L} height={barH} rx={5} />
          </clipPath>
          <g clipPath={`url(#er-${view.strategy.id})`}>
            {runs.map((r, i) => (
              <rect
                key={i}
                x={r.from}
                y={barTop}
                width={Math.max(r.to - r.from, 0)}
                height={barH}
                className={r.win ? 'fill-profit-soft' : 'fill-loss-soft'}
              />
            ))}
          </g>
          {runs
            .filter((r) => r.to - r.from > 70)
            .filter((r) => Math.abs((r.from + r.to) / 2 - x(spot)) > 40)
            .map((r, i) => (
              <text
                key={i}
                x={(r.from + r.to) / 2}
                y={barMid + 4}
                textAnchor="middle"
                className={`text-[12px] font-medium ${r.win ? 'fill-profit' : 'fill-loss'}`}
              >
                {r.win ? 'profit' : 'loss'}
              </text>
            ))}

          {/* Every strike: short ones solid, long ones — your protection —
              dashed. */}
          {strikes.map((s) => {
            const hit = s.short && s.price >= low && s.price <= high
            const at = x(s.price)
            return (
              <line
                key={`${s.right}${s.price}${s.short}`}
                x1={at}
                x2={at}
                y1={barTop - 3}
                y2={barTop + barH + 3}
                className={hit ? 'stroke-tested' : s.short ? 'stroke-ink' : 'stroke-muted'}
                strokeWidth={2}
                strokeDasharray={s.short ? undefined : '3 3'}
              />
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

          {placed.map((tag, i) => (
            <text
              key={i}
              x={tag.cx}
              y={below + tag.row * 18}
              textAnchor="middle"
              className={`text-[13px] ${tag.cls}`}
            >
              {tag.text}
            </text>
          ))}
        </svg>
      )}

      {/* The distances, in words and in the underlying's own units. */}
      <p className="mt-2 text-[15px] leading-relaxed text-ink">
        {needs.length > 0 && <>Price {needs.join(', or ')}. </>}
        The market expects a move of about ±{price(move, spot)} by {by}.
      </p>
      <p className="mt-1 text-[14px] text-muted">
        The blue bracket is one expected move either way — the market’s own number, not a
        forecast; price ends inside it about two times in three. The line is green where the
        whole position, every leg included, makes money if price ends there at expiry, and red
        where it loses. Dashed strikes are long options — your protection.
      </p>
    </div>
  )
}
