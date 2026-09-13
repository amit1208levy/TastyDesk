import { money, decimals, strike, fullDate, num, EM_DASH } from '../lib/format'
import type { Leg } from '../types'

function legName(leg: Leg): string {
  if (!leg.option_type) return leg.symbol
  const kind = leg.option_type === 'C' ? 'call' : 'put'
  return `${strike(leg.strike)} ${kind}`
}

/* Per-leg numbers, shown because a trader wants to see the structure — and
   labelled, unmissably, as detail rather than signal.

   A short put inside a credit spread routinely shows a far worse percentage
   move than the spread it belongs to, because the long put gained at the same
   moment. That number is real but it is not risk, and every alarm in this
   application is computed one level up, on the strategy. */
export function LegDetail({ legs }: { legs: Leg[] }) {
  const sorted = [...legs].sort((a, b) => {
    const ea = a.expiration ?? ''
    const eb = b.expiration ?? ''
    if (ea !== eb) return ea < eb ? -1 : 1
    if (a.option_type !== b.option_type) return (a.option_type ?? '') < (b.option_type ?? '') ? -1 : 1
    return (num(a.strike) ?? 0) - (num(b.strike) ?? 0)
  })

  return (
    <div className="rounded-card border border-line bg-sunken/60 p-3">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-faint">Legs</span>
        <span className="text-[11px] text-faint">
          detail only — risk is measured on the whole strategy, never on one leg
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-faint">
              <th className="pb-1.5 pr-3 font-medium">Leg</th>
              <th className="pb-1.5 pr-3 font-medium">Expiry</th>
              <th className="pb-1.5 pr-3 text-right font-medium">Qty</th>
              <th className="pb-1.5 pr-3 text-right font-medium">Open</th>
              <th className="pb-1.5 pr-3 text-right font-medium">Mark</th>
              <th className="pb-1.5 pr-3 text-right font-medium">Delta</th>
              <th className="pb-1.5 pr-3 text-right font-medium">Theta</th>
              <th className="pb-1.5 text-right font-medium">IV</th>
            </tr>
          </thead>
          <tbody className="num">
            {sorted.map((leg) => {
              const short = leg.direction === 'Short'
              return (
                <tr key={leg.symbol} className="border-t border-line/70">
                  <td className="py-1.5 pr-3 whitespace-nowrap">
                    <span
                      className={`mr-1.5 inline-block w-9 rounded px-1 text-center text-[10px] uppercase ${
                        short ? 'bg-accent-soft text-accent' : 'bg-sunken text-muted'
                      }`}
                    >
                      {short ? 'short' : 'long'}
                    </span>
                    {legName(leg)}
                  </td>
                  <td className="py-1.5 pr-3 whitespace-nowrap text-muted">{fullDate(leg.expiration)}</td>
                  <td className="py-1.5 pr-3 text-right">{decimals(leg.quantity, 0)}</td>
                  <td className="py-1.5 pr-3 text-right text-muted">{money(leg.open_price)}</td>
                  <td className="py-1.5 pr-3 text-right">{leg.mark === null ? EM_DASH : money(leg.mark)}</td>
                  <td className="py-1.5 pr-3 text-right text-muted">{decimals(leg.delta, 3)}</td>
                  <td className="py-1.5 pr-3 text-right text-muted">{decimals(leg.theta, 2)}</td>
                  <td className="py-1.5 text-right text-muted">
                    {leg.iv === null ? EM_DASH : `${(num(leg.iv)! * 100).toFixed(1)}%`}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
