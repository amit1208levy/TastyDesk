import { ErrorPanel, Loading, SectionHeading } from './States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, decimals, num, fullDate, EM_DASH } from '../lib/format'
import type { LossShapeReport, LossShapeGroup,
  Period,
} from '../types'

/* Win rate cannot show how a strategy loses, and the fix depends on it.

   A group whose worst ten trades carry 95% of its losses has a sizing or stop
   problem: ten decisions to change. A group where the damage is spread over
   sixty losses has a structural one — the trade itself does not pay enough for
   what it risks. Same win rate, different problem, opposite remedy. */

function Ratio({ value }: { value: string | null }) {
  const n = num(value)
  if (n === null) return <span className="text-faint">{EM_DASH}</span>
  const tone = n >= 1 ? 'text-profit' : n >= 0.5 ? 'text-watch' : 'text-loss'
  return <span className={`num ${tone}`}>{n.toFixed(2)}</span>
}

function Concentration({ group }: { group: LossShapeGroup }) {
  const worst10 = group.concentration.find(([n]) => n === 10)?.[1]
  const worst5 = group.concentration.find(([n]) => n === 5)?.[1]
  const share = worst10 ?? worst5
  if (share === undefined) return <span className="text-[13px] text-faint">too few losses</span>

  const label = worst10 !== undefined ? 'worst 10' : 'worst 5'
  const tight = share >= 0.8
  return (
    <div className="min-w-[110px]">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-sunken">
        <div
          className={tight ? 'h-full bg-watch' : 'h-full bg-loss/60'}
          style={{ width: `${Math.min(100, share * 100)}%` }}
        />
      </div>
      <div className="mt-0.5 text-[12px] text-faint">
        {label} = {pct(share, 0)} of losses
      </div>
    </div>
  )
}

function Verdict({ group }: { group: LossShapeGroup }) {
  const worst10 = group.concentration.find(([n]) => n === 10)?.[1]
  const ratio = num(group.win_loss_ratio)
  const net = num(group.net) ?? 0

  if (net >= 0) {
    if (ratio !== null && ratio >= 1) return <>Wins bigger than it loses. Leave it alone.</>
    return <>Wins often enough to carry a poor win/loss ratio.</>
  }
  if (worst10 !== undefined && worst10 >= 0.8) {
    return (
      <>
        A handful of trades did nearly all the damage — that is a sizing or stop question, not a
        reason to drop the strategy.
      </>
    )
  }
  return (
    <>
      The losses are spread out, so this is the trade itself: it does not pay enough for what it
      risks.
    </>
  )
}

export function LossShape({ period }: { period?: Period }) {
  const { data, error, loading, reload } = useAsync(() => api.lossShape(period), [period])

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Breaking down the losing side" />
  if (!data) return null

  const report = data as LossShapeReport
  const groups = Object.entries(report.by_strategy)

  return (
    <section>
      <SectionHeading
        title="How each strategy loses"
        hint="a few disasters and a steady bleed need opposite fixes"
      />

      <div className="overflow-hidden sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-[16px]">
            <thead>
              <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
                <th className="py-3.5 pl-4 pr-3 font-medium">Strategy</th>
                <th className="py-3.5 pr-3 text-right font-medium">Trades</th>
                <th className="py-3.5 pr-3 text-right font-medium">Loses</th>
                <th className="py-3.5 pr-3 text-right font-medium">Avg win</th>
                <th className="py-3.5 pr-3 text-right font-medium">Avg loss</th>
                <th
                  className="py-3.5 pr-3 text-right font-medium"
                  title="Average win divided by average loss. Above 1 means it wins more than it gives back."
                >
                  W / L
                </th>
                <th className="py-3.5 pr-3 font-medium">Where the losses are</th>
                <th className="py-3.5 pr-4 text-right font-medium">Net</th>
              </tr>
            </thead>
            <tbody className="rows stagger">
              {groups.map(([name, g]) => (
                <tr key={name} className="border-b border-line/60 align-top">
                  <td className="py-3.5 pl-4 pr-3">
                    <div className="font-medium">{name}</div>
                    <div className="mt-0.5 max-w-[280px] text-[13px] leading-snug text-muted">
                      <Verdict group={g} />
                    </div>
                  </td>
                  <td className="num py-3.5 pr-3 text-right">{g.trades}</td>
                  <td className="num py-3.5 pr-3 text-right">
                    {g.loss_rate === null ? EM_DASH : pct(g.loss_rate, 0)}
                    <div className="text-[12px] text-faint">{g.losses} of {g.trades}</div>
                  </td>
                  <td className="num py-3.5 pr-3 text-right text-profit">
                    {money(g.avg_win, { cents: false })}
                  </td>
                  <td className="num py-3.5 pr-3 text-right text-loss">
                    {money(g.avg_loss, { cents: false })}
                  </td>
                  <td className="py-3.5 pr-3 text-right">
                    <Ratio value={g.win_loss_ratio} />
                  </td>
                  <td className="py-3.5 pr-3">
                    <Concentration group={g} />
                  </td>
                  <td
                    className={`num py-3.5 pr-4 text-right font-medium ${
                      (num(g.net) ?? 0) >= 0 ? 'text-profit' : 'text-loss'
                    }`}
                  >
                    {money(g.net, { sign: true, cents: false })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {report.overall.worst.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] text-muted hover:text-ink">
            The {report.overall.worst.length} worst trades in the book
          </summary>
          <div className="mt-2 overflow-hidden sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
            {report.overall.worst.map((w) => (
              <div
                key={w.id}
                className="flex flex-wrap items-baseline gap-2 border-b border-line/60 px-3.5 py-3 text-[14px] last:border-0"
              >
                <span className="w-16 shrink-0 font-medium">{w.underlying}</span>
                <span className="w-40 shrink-0 text-muted">{w.structure}</span>
                <span className="text-faint">{fullDate(w.opened)}</span>
                <span className="text-faint">
                  held {w.days_held === null ? EM_DASH : `${w.days_held}d`}
                </span>
                <span className="num ml-auto text-loss">
                  {money(w.realized_pnl, { sign: true, cents: false })}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}

      <p className="mt-2 text-[13px] text-faint">
        Across everything: {report.overall.wins} wins worth{' '}
        {money(report.overall.gross_won, { cents: false })} against {report.overall.losses} losses
        worth {money(report.overall.gross_lost, { cents: false })}, for{' '}
        {money(report.overall.net, { sign: true, cents: false })}. Average win{' '}
        {money(report.overall.avg_win, { cents: false })}, average loss{' '}
        {money(report.overall.avg_loss, { cents: false })} —{' '}
        {decimals(report.overall.win_loss_ratio, 2)} to one.
      </p>
    </section>
  )
}
