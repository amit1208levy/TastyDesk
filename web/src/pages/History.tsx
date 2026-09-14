import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, fullDate, num, signedClass, EM_DASH } from '../lib/format'

export function History() {
  const { data, error, loading, reload } = useAsync(() => api.closedStrategies(300), [])

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Loading closed trades" />
  if (!data || data.length === 0) {
    return <Empty title="No closed trades yet." hint="They appear here as positions are closed out." />
  }

  const totalPnl = data.reduce((acc, v) => acc + (num(v.strategy.realized_pnl) ?? 0), 0)

  return (
    <div className="space-y-4">
      <SectionHeading
        title="Closed trades"
        hint={`${data.length} shown · realized P&L net of fees`}
        right={
          <span className={`num text-sm font-semibold ${signedClass(totalPnl)}`}>
            {money(totalPnl, { sign: true, cents: false })}
          </span>
        }
      />

      <div className="overflow-x-auto rounded-card border border-line bg-raised">
        <table className="w-full min-w-[820px] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-[10px] uppercase tracking-wider text-faint">
              <th className="py-2 pl-4 pr-3 font-medium">Closed</th>
              <th className="py-2 pr-3 font-medium">Underlying</th>
              <th className="py-2 pr-3 font-medium">Strategy</th>
              <th className="py-2 pr-3 text-right font-medium">Credit</th>
              <th className="py-2 pr-3 text-right font-medium">Realized</th>
              <th className="py-2 pr-3 text-right font-medium" title="Share of the credit kept">
                % of credit
              </th>
              <th className="py-2 pr-3 text-right font-medium">Days</th>
              <th className="py-2 pr-4 text-right font-medium">Rolls</th>
            </tr>
          </thead>
          <tbody className="num">
            {data.map((v) => {
              const s = v.strategy
              const credit = num(s.net_credit) ?? 0
              const realized = num(s.realized_pnl) ?? 0
              const share = credit !== 0 ? realized / Math.abs(credit) : null
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
                  <td className="py-2 pl-4 pr-3 whitespace-nowrap text-muted">{fullDate(s.closed_at)}</td>
                  <td className="py-2 pr-3 font-medium">{s.underlying}</td>
                  <td className="py-2 pr-3 text-muted">{s.strategy_type}</td>
                  <td className="py-2 pr-3 text-right text-muted">{money(s.net_credit, { cents: false })}</td>
                  <td className={`py-2 pr-3 text-right font-medium ${signedClass(realized)}`}>
                    {money(realized, { sign: true })}
                  </td>
                  <td className={`py-2 pr-3 text-right ${signedClass(share)}`}>
                    {share === null ? EM_DASH : pct(share, 0, true)}
                  </td>
                  <td className="py-2 pr-3 text-right text-muted">{days ?? EM_DASH}</td>
                  <td className="py-2 pr-4 text-right text-muted">{s.roll_count || EM_DASH}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
