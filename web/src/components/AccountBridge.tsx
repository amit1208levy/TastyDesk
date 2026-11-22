import type { ReactNode } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, num, shortDate } from '../lib/format'
import type { Period } from '../types'

/* Where the account's change came from, the way a trader says it: what you
   banked on trades you closed, what your open trades are up or down, and the
   small rest. Every figure is only what happened inside the period, so the
   three add up to the account (22 Nov 2026: the old seven-line walk from the
   trade total was hard to follow). Why the trade total differs is one note. */
export function AccountBridge({ period, now }: { period: Period; now: boolean }) {
  const { data: b } = useAsync(() => api.accountBridge(period), [period.from, period.to, now])
  if (!b) return null

  const account = num(b.account) ?? 0
  const closed = num(b.closed) ?? 0
  const open = num(b.open_now) ?? 0
  const other = num(b.other) ?? 0
  const reported = num(b.reported) ?? 0
  // The period's own first day: the day after the close it is measured from.
  const opens = new Date(`${b.start}T12:00:00`)
  opens.setDate(opens.getDate() + 1)
  const from = shortDate(opens.toISOString().slice(0, 10))
  const fmt = (v: number) => money(v, { sign: true, cents: false })
  const tone = (v: number) => (v > 0 ? 'text-profit' : v < 0 ? 'text-loss' : 'text-muted')

  const line = (label: string, value: number, note: ReactNode, strong = false) => (
    <li className={`flex items-baseline justify-between gap-4 py-3 ${strong ? 'border-t-2 border-line-strong' : ''}`}>
      <div className="min-w-0">
        <div className={strong ? 'text-copy font-semibold' : 'text-copy'}>{label}</div>
        <div className="mt-0.5 text-note text-muted">{note}</div>
      </div>
      <div className={`figure shrink-0 ${strong ? 'text-lead font-semibold' : 'text-body'} ${tone(value)}`}>
        {fmt(value)}
      </div>
    </li>
  )

  const why: string[] = []
  if ((num(b.earlier) ?? 0) !== 0) why.push(`it counts each trade's whole result, even the part from before ${from}`)
  if (b.open_trades > 0) why.push(`it leaves out ${b.open_trades} open trade${b.open_trades === 1 ? '' : 's'}`)

  return (
    <section className="surface sheened px-4 py-4 sm:px-6 sm:py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="label">Where the account’s change came from</div>
        <div className="text-note text-faint">since {from}</div>
      </div>
      <ul className="mt-1 divide-y divide-line">
        {line(
          'Banked',
          closed,
          `${b.closed_trades} trade${b.closed_trades === 1 ? '' : 's'} you closed since ${from}`,
        )}
        {line(
          'Open trades',
          open,
          `${b.open_now_trades} still open: up or down since ${from}, at today’s prices`,
        )}
        {line(
          'Everything else',
          other,
          <>
            Fees, interest, the broker pricing a little differently
            {b.left_out_trades > 0 && `, and ${b.left_out_trades} old-self trade${b.left_out_trades === 1 ? '' : 's'}`}
          </>,
        )}
        {line('The account', account, '"Made trading" at the top: net liq change, deposits taken out', true)}
      </ul>
      {why.length > 0 && (
        <p className="mt-2 text-note text-faint">
          The trades total above says {fmt(reported)} because {why.join(', and ')}.
        </p>
      )}
      {b.unsplit > 0 && (
        <p className="mt-1 text-note text-faint">
          {b.unsplit} trade{b.unsplit === 1 ? ' was' : 's were'} open on {from} with no price on record, so all of
          {b.unsplit === 1 ? ' its' : ' their'} result is counted here.
        </p>
      )}
    </section>
  )
}
