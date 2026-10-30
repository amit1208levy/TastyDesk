import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, num, shortDate } from '../lib/format'
import type { Period } from '../types'

/* The winners he did not take. Each one reached the profit target while open
   and closed for less: the money between the two is what waiting cost. Read
   from the app's own marks, so it is a floor, and only as far back as those go. */
export function GivenBackCard({ period }: { period: Period }) {
  const { data } = useAsync(() => api.givenBack(period), [period.from, period.to])
  if (!data || data.tracked === 0) return null
  const lost = num(data.given_back) ?? 0
  return (
    <section className="surface sheened px-6 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="label">Winners you let slip</div>
        <div className="text-[13px] text-faint">
          {data.tracked} closed trades with marks on record · target 50% of credit
        </div>
      </div>
      <p className="mt-2 text-[17px] leading-relaxed">
        {data.reached === 0 ? (
          'None of them reached the target while open.'
        ) : data.closed_below === 0 ? (
          <>
            <span className="num font-medium text-profit">{data.reached}</span> reached the target, and
            every one was taken at it or better.
          </>
        ) : (
          <>
            <span className="num font-medium">{data.reached}</span> reached the target;{' '}
            <span className="num font-medium text-loss">{data.closed_below}</span> closed back under it,
            giving back <span className="num font-medium text-loss">{money(lost, { cents: false })}</span>.
          </>
        )}
      </p>
      {data.worst.length > 0 && (
        <ul className="mt-3 space-y-1 text-[14px] text-muted">
          {data.worst.map((w) => (
            <li key={w.strategy_id} className="num">
              {w.underlying} · closed {shortDate(w.closed)} · was up {money(w.peak, { cents: false })}, banked{' '}
              {money(w.banked, { sign: true, cents: false })}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
