import { money, decimals, num, EM_DASH } from '../lib/format'
import type { GreekTotals } from '../types'

/* Directional risk in units that add up.

   A delta of 0.20 on a /ZB option and a delta of 0.20 on an XLE option are not
   the same amount of risk — one point of /ZB is $1,000 and one point of XLE is
   $100 — so the column that matters is money, and the total that matters is
   money restated in SPY. Anything the data cannot support is named rather than
   filled in. */
export function Exposure({ g }: { g: GreekTotals }) {
  const spy = num(g.beta_weighted_delta)
  const rows = g.by_underlying

  return (
    <div className="sheened rounded-card border border-line bg-raised shadow-[var(--shadow-md)]">
      <div className="flex flex-wrap items-baseline gap-2 border-b border-line px-4 py-3.5">
        <h3 className="display text-[19px]">Exposure by product</h3>
        <span className="text-[13px] text-faint">
          beta-weighted to {g.reference_symbol}
          {g.reference_price ? ` at ${money(g.reference_price, { cents: false })}` : ''}
        </span>
        <span className="num ml-auto text-[14px]">
          <span className={spy === null ? 'text-faint' : spy >= 0 ? 'text-profit' : 'text-loss'}>
            {spy === null ? EM_DASH : `${decimals(spy, 1)} ${g.reference_symbol}`}
          </span>
          <span className="text-faint"> · </span>
          <span className="text-muted">
            {money(g.dollars_per_spy_percent, { sign: true, cents: false })} per 1%
          </span>
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-[14px]">
          <thead>
            <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
              <th className="py-3 pl-4 pr-3 font-medium">Product</th>
              <th className="py-3 pr-3 text-right font-medium">Beta</th>
              <th className="py-3 pr-3 text-right font-medium">Price</th>
              <th className="py-3 pr-3 text-right font-medium">{g.reference_symbol} delta</th>
              <th className="py-3 pr-3 text-right font-medium">Theta / day</th>
              <th className="py-3 pr-4 text-right font-medium">Vega</th>
            </tr>
          </thead>
          <tbody className="rows stagger">
            {rows.map((r) => {
              const s = num(r.beta_weighted_delta)
              return (
                <tr key={r.product} className="border-b border-line/60 last:border-0 hover:bg-hover">
                  <td className="py-3.5 pl-4 pr-3 font-medium">
                    {r.product}
                    <span className="ml-1.5 text-[12px] text-faint">
                      {r.strategies} open
                      {r.legs_missing_delta > 0 && (
                        <span className="text-loss"> · {r.legs_missing_delta} unpriced</span>
                      )}
                    </span>
                  </td>
                  <td className="num py-3.5 pr-3 text-right text-muted">
                    {r.beta === null ? (
                      <span className="text-loss">unknown</span>
                    ) : (
                      decimals(r.beta, 2)
                    )}
                  </td>
                  <td className="num py-3.5 pr-3 text-right text-faint">
                    {/* One line per contract month. A product held in two
                        months has two prices, and showing a dash because they
                        disagree looks like missing data. */}
                    {r.months.length <= 1 ? (
                      r.months[0]?.[1] == null ? (
                        EM_DASH
                      ) : (
                        decimals(r.months[0][1], 2)
                      )
                    ) : (
                      <span className="flex flex-col items-end leading-tight">
                        {r.months.map(([symbol, price]) => (
                          <span key={symbol} className="text-[15px] leading-snug">
                            <span className="mr-1.5 text-[12px] text-faint">{symbol}</span>
                            {price == null ? EM_DASH : decimals(price, 2)}
                          </span>
                        ))}
                      </span>
                    )}
                  </td>
                  <td
                    className={`num py-3.5 pr-3 text-right font-medium ${
                      s === null ? 'text-faint' : s >= 0 ? 'text-profit' : 'text-loss'
                    }`}
                  >
                    {s === null ? EM_DASH : decimals(s, 1)}
                  </td>
                  <td className="num py-3.5 pr-3 text-right text-muted">
                    {money(r.theta, { sign: true, cents: false })}
                  </td>
                  <td className="num py-3.5 pr-4 text-right text-muted">
                    {money(r.vega, { sign: true, cents: false })}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="border-t border-line px-4 py-3 text-[12px] text-faint">
        {g.reference_symbol} delta is your exposure in that product restated through its beta, so
        the column adds up across everything you hold and the total is what the book behaves like in{' '}
        {g.reference_symbol}.
        {g.missing_beta.length > 0 && (
          <span className="text-loss">
            {' '}
            No beta for {g.missing_beta.join(', ')} — left out of the weighted total rather than
            assumed.
          </span>
        )}
        {g.missing_price.length > 0 && (
          <span className="text-loss"> No price for {g.missing_price.join(', ')}.</span>
        )}
        {g.missing_delta.length > 0 && (
          <span className="text-loss">
            {' '}
            {g.missing_delta.length} leg{g.missing_delta.length === 1 ? '' : 's'} without a delta.
          </span>
        )}
      </p>
    </div>
  )
}
