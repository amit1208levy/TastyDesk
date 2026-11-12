import type { ReactNode } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, num, shortDate } from '../lib/format'
import type { Period } from '../types'

/* The account said +$1,003 for November and the trades beneath it -$2,373, and
   both were right. A trade total counts a trade's whole result on the day it
   closes, even the part from before the period, and leaves open trades out;
   the account counts every trade, but only what it did inside the period.
   This walks from one figure to the other, a line at a time. */
export function AccountBridge({ period, now }: { period: Period; now: boolean }) {
  const { data: b } = useAsync(() => api.accountBridge(period), [period.from, period.to, now])
  if (!b) return null

  const earlier = num(b.earlier) ?? 0
  const cash = num(b.cash) ?? 0
  // The period's own first day: the day after the close it is measured from.
  const opens = new Date(`${b.start}T12:00:00`)
  opens.setDate(opens.getDate() + 1)
  const from = shortDate(opens.toISOString().slice(0, 10))

  const line = (label: string, value: number, note: ReactNode, strong = false) => (
    <li className={`flex items-baseline justify-between gap-4 py-2.5 ${strong ? 'border-t border-line-strong' : ''}`}>
      <div className="min-w-0">
        <div className={strong ? 'text-copy font-semibold' : 'text-copy'}>{label}</div>
        <div className="mt-0.5 text-note text-muted">{note}</div>
      </div>
      <div
        className={`figure shrink-0 ${strong ? 'text-lead font-semibold' : 'text-copy'} ${
          value > 0 ? 'text-profit' : value < 0 ? 'text-loss' : 'text-muted'
        }`}
      >
        {money(value, { sign: true, cents: false })}
      </div>
    </li>
  )

  return (
    <section className="surface sheened px-4 py-4 sm:px-6 sm:py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="label">Why the account and the trades show different numbers</div>
        <div className="text-note text-faint">
          from the {shortDate(b.start)} close to {shortDate(b.end)}
        </div>
      </div>
      <ul className="mt-2 divide-y divide-line">
        {line(
          'Trades total, as shown above',
          num(b.reported) ?? 0,
          <>
            {b.reported_trades} trades
            {b.reported_open > 0 && ` · ${b.reported_open} still open, counted at today's price`}
          </>,
        )}
        {earlier !== 0 &&
          line(
            `Take out what happened before ${from}`,
            -earlier,
            'A trade counts its whole result on the day it closes. This part was made or lost earlier, so the account already had it.',
          )}
        {b.open_trades > 0 &&
          line(
            'Add your other open trades',
            num(b.open_change) ?? 0,
            `${b.open_trades} open trades the total leaves out: what they made or lost since ${from}.`,
          )}
        {b.left_out_trades > 0 &&
          line(
            'Add old-self trades closed in this time',
            num(b.left_out) ?? 0,
            `${b.left_out_trades} trades New Levy does not count.`,
          )}
        {Math.abs(cash) >= 0.5 && line('Interest and adjustments', cash, 'Money that belongs to no trade.')}
        {line(
          'Price differences and fees',
          num(b.rest) ?? 0,
          'Net liq uses the broker’s own prices. The trades use the middle of bid and ask, read when the app last looked.',
        )}
        {line('The account', num(b.account) ?? 0, 'Net liq change with deposits taken out: "Made trading" at the top.', true)}
      </ul>
      {b.unsplit > 0 && (
        <p className="mt-2 text-note text-faint">
          {b.unsplit} trade{b.unsplit === 1 ? ' was' : 's were'} open at the start with no price on record, so the part
          from before {from} could not be taken out.
        </p>
      )}
    </section>
  )
}
