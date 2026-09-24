import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { RollCandidates } from '../components/RollCandidates'
import { NeedsReview } from '../components/NeedsReview'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, fullDate, num, signedClass, EM_DASH } from '../lib/format'
import type { StrategyView } from '../types'

/* What each year came to, and what is still riding on this one.

   The figure at the top of this page is realized: money actually taken, net of
   fees, on trades that are finished. It is the honest number for a year that is
   over and an incomplete one for the year you are in, where the open positions
   are neither counted nor visible. So the current year shows all three — what
   was banked, what is still open, and the two together — and a finished year
   shows only what was banked, because nothing is riding on it any more. */
function ByYear({ views, open }: { views: StrategyView[]; open: string | null }) {
  const thisYear = new Date().getFullYear()
  const realized = new Map<number, number>()
  for (const v of views) {
    const closed = v.strategy.closed_at
    if (!closed) continue
    const year = new Date(closed).getFullYear()
    realized.set(year, (realized.get(year) ?? 0) + (num(v.strategy.realized_pnl) ?? 0))
  }
  const years = [...realized.keys()].sort((a, b) => b - a)
  if (years.length === 0) return null
  const openPnl = num(open)

  return (
    <div className="sheened rounded-card border border-line bg-raised px-4 py-3 shadow-[var(--shadow-sm)]">
      <div className="label">By year — realized is money taken, net of fees</div>
      <ul className="mt-2 space-y-1.5">
        {years.map((year) => {
          const banked = realized.get(year) ?? 0
          const running = year === thisYear
          const all = openPnl === null ? null : banked + openPnl
          return (
            <li key={year} className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[16px]">
              <span className="num w-12 shrink-0 font-semibold">{year}</span>
              <span>
                <span className={`figure font-medium ${signedClass(banked)}`}>
                  {money(banked, { sign: true, cents: false })}
                </span>
                <span className="text-muted"> realized</span>
              </span>
              {running && (
                <>
                  <span>
                    <span className={`figure font-medium ${signedClass(open)}`}>
                      {money(open, { sign: true, cents: false })}
                    </span>
                    <span className="text-muted"> still open</span>
                  </span>
                  <span>
                    <span className={`figure font-semibold ${signedClass(all)}`}>
                      {all === null ? EM_DASH : money(all, { sign: true, cents: false })}
                    </span>
                    <span className="text-muted"> the year so far</span>
                  </span>
                  {openPnl === null && (
                    <span className="text-[14px] text-warn">
                      one position has no price, so the two cannot be added
                    </span>
                  )}
                </>
              )}
            </li>
          )
        })}
      </ul>
      <p className="mt-2 text-[14px] text-muted">
        A finished year shows only what was banked — nothing is riding on it any more. What is
        still open is a mark that can move, not a result.
      </p>
    </div>
  )
}

export function History() {
  const { data, error, loading, reload } = useAsync(() => api.closedStrategies(2000), [])
  // Only for the open figure beside this year's realized total.
  const summary = useAsync(() => api.summary(), [])

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Loading closed trades" />
  if (!data || data.length === 0) {
    return <Empty title="No closed trades yet." hint="They appear here as positions are closed out." />
  }

  const totalPnl = data.reduce((acc, v) => acc + (num(v.strategy.realized_pnl) ?? 0), 0)

  return (
    <div className="space-y-4">
      <NeedsReview />

      <RollCandidates onChange={reload} />

      <ByYear views={data} open={summary.data?.open_pnl ?? null} />

      <SectionHeading
        title="Closed trades"
        hint={`${data.length} shown · realized only — what you took, net of fees`}
        right={
          <span className="flex items-baseline gap-2">
            <span className="text-[13px] text-muted">every year shown</span>
            <span className={`num text-[16px] font-semibold ${signedClass(totalPnl)}`}>
              {money(totalPnl, { sign: true, cents: false })}
            </span>
          </span>
        }
      />

      <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
        <table className="w-full min-w-[820px] text-[16px]">
          <thead>
            <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
              <th className="py-3 pl-4 pr-3 font-medium">Closed</th>
              <th className="py-3 pr-3 font-medium">Underlying</th>
              <th className="py-3 pr-3 font-medium">Strategy</th>
              <th className="py-3 pr-3 text-right font-medium">Credit</th>
              <th className="py-3 pr-3 text-right font-medium">Realized</th>
              <th className="py-3 pr-3 text-right font-medium" title="Share of the credit kept">
                % of credit
              </th>
              <th className="py-3 pr-3 text-right font-medium">Days</th>
              <th className="py-3 pr-4 text-right font-medium">Rolls</th>
            </tr>
          </thead>
          <tbody className="num">
            {data.map((v) => {
              const s = v.strategy
              const realized = num(s.realized_pnl) ?? 0
              // Computed on the server, which knows when the question has no
              // answer: an outright futures contract has no premium to be a
              // percentage of, and dividing by whatever sits in net_credit
              // produced rows reading "+22,736% of credit".
              const share = num(v.pnl.realized_pct_of_credit)
              const days =
                s.closed_at && s.opened_at
                  ? Math.max(
                      0,
                      Math.round(
                        (new Date(s.closed_at).getTime() - new Date(s.opened_at).getTime()) / 86_400_000,
                      ),
                    )
                  : null
              return (
                <tr key={s.id} className="border-b border-line/60 last:border-0 hover:bg-hover">
                  <td className="py-3 pl-4 pr-3 whitespace-nowrap text-muted">{fullDate(s.closed_at)}</td>
                  <td className="py-3 pr-3 font-medium">{s.underlying}</td>
                  <td className="py-3 pr-3 text-muted">{s.strategy_type}</td>
                  <td className="py-3 pr-3 text-right text-muted">{money(s.net_credit, { cents: false })}</td>
                  <td className={`py-3 pr-3 text-right font-medium ${signedClass(realized)}`}>
                    {money(realized, { sign: true })}
                  </td>
                  <td
                    className={`py-3 pr-3 text-right ${
                      share !== null && Math.abs(share) > 10 ? 'text-faint' : signedClass(share)
                    }`}
                    title={
                      share !== null && Math.abs(share) > 10
                        ? `This trade's net credit was only ${money(s.net_credit)}, so the result as a share of it is not a scale worth reading.`
                        : undefined
                    }
                  >
                    {share === null
                      ? EM_DASH
                      : Math.abs(share) > 10
                        ? `${share > 0 ? '>' : '<-'}999%`
                        : pct(share, 0, true)}
                  </td>
                  <td className="py-3 pr-3 text-right text-muted">{days ?? EM_DASH}</td>
                  <td className="py-3 pr-4 text-right text-muted">{s.roll_count || EM_DASH}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
