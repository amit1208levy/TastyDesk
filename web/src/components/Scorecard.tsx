import type { ReactNode } from 'react'
import { StatTile } from './StatTile'
import { ZoneBar } from '../pages/Tom'
import { decimals, EM_DASH, money, num, pct } from '../lib/format'
import type { PacePerformance, PerformanceStats, Period } from '../types'

// Plan §1: "Generate 2–3% income monthly (25–30% annual) on Net Liquidation value".
const TOM_MONTH_LOW = 0.02
const TOM_MONTH_HIGH = 0.03
// Under this many trades a win rate is a coin's last few tosses.
const THIN = 20

const cash = (v: number, sign = true) => money(v, { sign, cents: Math.abs(v) < 100 })

/* What the trades add up to, said in the order a person asks it: how much,
   is that enough, and where does it come from. Ten equal tiles answered none
   of those: with two trades on the page they projected a year from them, and
   two of the ten were a dash. */
export function Scorecard({
  o,
  pace,
  now,
  period,
  netLiq,
}: {
  o: PerformanceStats
  pace: PacePerformance | null
  now: boolean
  period: Period
  netLiq: number | null
}) {
  const n = o.trades
  const thin = n < THIN
  const total = num(o.total_pnl) ?? 0
  const each = num(o.expectancy) ?? 0
  const avgWin = Math.abs(num(o.avg_win) ?? 0)
  const avgLoss = Math.abs(num(o.avg_loss) ?? 0)
  const perMonth = num(pace?.per_month ?? null)
  const perYear = num(pace?.per_year ?? null)
  const tradesAMonth = num(pace?.trades_per_month ?? null)
  const ofNetLiq = perMonth !== null && netLiq ? perMonth / netLiq : null
  // The win rate at which the average win and the average loss cancel out.
  const needed = o.wins > 0 && o.losses > 0 && avgWin + avgLoss > 0 ? avgLoss / (avgWin + avgLoss) : null
  const lowTarget = TOM_MONTH_LOW * (netLiq ?? 0)
  const highTarget = TOM_MONTH_HIGH * (netLiq ?? 0)

  let verdict: ReactNode
  if (n === 0) {
    verdict = 'No trades closed in this period.'
  } else if (thin) {
    verdict = (
      <>
        Only {n} trade{n === 1 ? '' : 's'} here: {o.wins} won, {o.losses} lost. That is too few to trust a win rate or
        to project a year from. The picture settles after about {THIN}.
      </>
    )
  } else if (each >= 0) {
    verdict = (
      <>
        You win {pct(o.win_rate, 0)} of your trades and make {cash(each, false)} on the average one.
        {perMonth !== null && tradesAMonth !== null && (
          <>
            {' '}
            At {decimals(tradesAMonth, 0)} trades a month that is about {money(perMonth, { cents: false })} a month.
          </>
        )}
      </>
    )
  } else {
    verdict = (
      <>
        You win {pct(o.win_rate, 0)} of your trades and still lose {cash(Math.abs(each), false)} on the average one:
        the losses are bigger than the wins can pay for.
      </>
    )
  }

  const target =
    ofNetLiq === null
      ? null
      : ofNetLiq >= TOM_MONTH_HIGH
        ? 'Above Tom’s 2–3%.'
        : ofNetLiq >= TOM_MONTH_LOW
          ? 'Inside Tom’s 2–3%.'
          : `Tom’s 2–3% would be ${money(lowTarget, { cents: false })}–${money(highTarget, { cents: false })} a month.`

  return (
    <section className="space-y-2.5">
      <div className="surface sheened grid grid-cols-1 gap-6 px-4 py-4 sm:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] sm:gap-10 sm:px-6 sm:py-6">
        <div className="min-w-0">
          <div className="label">
            {now ? 'Result, open trades at today’s price' : 'Banked'} · {period.label}
          </div>
          <div
            className={`figure mt-2 text-[40px] leading-none sm:text-[48px] ${
              total > 0 ? 'text-profit' : total < 0 ? 'text-loss' : 'text-ink'
            }`}
          >
            {money(total, { sign: true, cents: false })}
          </div>
          <div className="num mt-2 text-note text-muted">
            {n} trade{n === 1 ? '' : 's'} · {o.wins} won · {o.losses} lost · net of fees
          </div>
          <p className="mt-4 text-lead leading-relaxed">{verdict}</p>
        </div>

        <div className="min-w-0">
          <div className="label">A month, against Tom’s target</div>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-2">
            <span className="figure text-heading leading-none">
              {perMonth === null ? EM_DASH : money(perMonth, { sign: true, cents: false })}
            </span>
            <span className="text-note text-muted">
              {ofNetLiq === null ? 'needs a monthly pace and net liq' : `${pct(ofNetLiq, 1)} of net liq`}
              {thin && n > 0 ? ' · thin sample' : ''}
            </span>
          </div>
          <div className="mt-6">
            <ZoneBar
              label="Your month as a share of net liq, against Tom's 2 to 3 percent"
              min={0}
              max={0.04}
              value={ofNetLiq}
              zones={[{ from: TOM_MONTH_LOW, to: TOM_MONTH_HIGH, className: 'bg-ok/45' }]}
              marks={[
                { at: 0, label: '0' },
                { at: TOM_MONTH_LOW, label: '2%' },
                { at: TOM_MONTH_HIGH, label: '3%' },
              ]}
            />
          </div>
          {target && <p className="mt-3 text-body leading-relaxed text-muted">{target}</p>}
        </div>
      </div>

      {n > 0 && (
        <div className="surface sheened grid grid-cols-1 gap-6 px-4 py-4 sm:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] sm:gap-10 sm:px-6 sm:py-6">
          <div className="min-w-0">
            <div className="label">Your edge</div>
            <div className="mt-2 flex flex-wrap items-baseline gap-x-2">
              <span className="figure text-heading leading-none">{pct(o.win_rate, 0)}</span>
              <span className="text-note text-muted">
                of trades won
                {needed !== null && ` · you need ${pct(needed, 0)} to break even`}
              </span>
            </div>
            <div className="mt-6">
              <ZoneBar
                label="Your win rate against the win rate that breaks even"
                min={0}
                max={1}
                value={o.win_rate}
                zones={needed === null ? [] : [{ from: needed, to: 1, className: 'bg-ok/45' }]}
                marks={needed === null ? [] : [{ at: needed, label: `break even ${pct(needed, 0)}` }]}
              />
            </div>
            <p className="mt-3 text-body leading-relaxed text-muted">
              {needed === null
                ? o.losses === 0
                  ? 'No losses yet, so there is no break-even to measure.'
                  : 'No wins yet.'
                : (o.win_rate ?? 0) >= needed
                  ? 'In the green: your wins pay for your losses with money left over.'
                  : 'Short of the green: at this win rate the losses cost more than the wins make.'}
            </p>
          </div>
          <dl className="grid min-w-0 grid-cols-2 content-start gap-x-6 gap-y-4">
            {(
              [
                ['Average win', o.wins ? cash(avgWin) : EM_DASH, 'text-profit'],
                ['Average loss', o.losses ? cash(-avgLoss) : EM_DASH, 'text-loss'],
                [
                  'One loss costs',
                  o.wins && o.losses && avgWin > 0 ? `${decimals(avgLoss / avgWin, 1)} wins` : EM_DASH,
                  'text-ink',
                ],
                [
                  'For every $1 lost',
                  o.profit_factor === null ? EM_DASH : `you made $${decimals(o.profit_factor, 2)}`,
                  'text-ink',
                ],
              ] as const
            ).map(([label, value, tone]) => (
              <div key={label} className="min-w-0">
                <dt className="text-note text-muted">{label}</dt>
                <dd className={`figure mt-1 text-title leading-tight ${tone}`}>{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {n > 0 && (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          <StatTile
            label="Trades a month"
            value={tradesAMonth === null ? EM_DASH : decimals(tradesAMonth, 1)}
            tone="muted"
            sub={
              thin || perYear === null
                ? 'too few for a yearly figure'
                : `${money(perYear, { sign: true, cents: false })} a year at this pace`
            }
            title="How often trades close, and what your average trade makes at that pace: expectancy × trades a month × 12"
          />
          <StatTile
            label="Return on BP"
            value={o.bp_return_month === null ? EM_DASH : `${pct(o.bp_return_month, 1)} a month`}
            tone={o.bp_return_month === null ? 'muted' : (num(o.bp_return_month) ?? 0) >= 0 ? 'profit' : 'loss'}
            sub={
              o.bp_return_month === null
                ? `on record for ${o.bp_return_month_n} of ${n} trades`
                : `on buying power tied up`
            }
            title="Everything made over everything tied up (buying power, or what a long option cost), for as long as it was tied up, scaled to a month. Buying power is logged daily since Oct 26."
          />
          <StatTile
            label="Days held"
            value={decimals(o.avg_days_in_trade, 0)}
            tone="muted"
            sub={
              o.avg_pct_of_max_profit_captured === null
                ? 'on average'
                : `winners kept ${pct(o.avg_pct_of_max_profit_captured, 0)} of max`
            }
          />
          <StatTile
            label="Biggest loss"
            value={o.largest_loss === null || o.losses === 0 ? EM_DASH : money(-Math.abs(num(o.largest_loss) ?? 0), { cents: false })}
            tone={o.losses === 0 ? 'muted' : 'loss'}
            sub={o.largest_win === null || o.wins === 0 ? 'no wins yet' : `biggest win ${money(o.largest_win, { sign: true, cents: false })}`}
          />
        </div>
      )}
    </section>
  )
}
