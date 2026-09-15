import { Fragment, useState } from 'react'
import { DangerBadge } from './DangerBadge'
import { RiskScale } from './RiskScale'
import { LegDetail } from './LegDetail'
import { PayoffPanel } from './PayoffPanel'
import { money, pctOfCredit, pct, decimals, dteLabel, signedClass, EM_DASH } from '../lib/format'
import type { StrategyView, DangerLevel } from '../types'
import { DANGER_ORDER } from '../types'

function rank(level: DangerLevel): number {
  return DANGER_ORDER.indexOf(level)
}

/* The open-positions table. Sorted by danger first, because the whole point of
   the dashboard is that the thing needing attention is at the top, and danger
   here is always the strategy-level assessment. */
export function StrategyTable({ views }: { views: StrategyView[] }) {
  const [expanded, setExpanded] = useState<string | null>(null)

  const sorted = [...views].sort((a, b) => {
    const d = rank(b.risk.level) - rank(a.risk.level)
    if (d !== 0) return d
    return b.risk.score - a.risk.score
  })

  if (sorted.length === 0) {
    return (
      <div className="rounded-card border border-dashed border-line bg-raised px-6 py-12 text-center">
        <div className="text-sm text-muted">No open strategies.</div>
        <div className="mt-1 text-xs text-faint">
          Positions appear here once a sync has run against your tastytrade account.
        </div>
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-card border border-line bg-raised">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-[10px] uppercase tracking-wider text-faint">
              <th className="py-2.5 pl-4 pr-3 font-medium">Underlying</th>
              <th className="py-2.5 pr-3 font-medium">Strategy</th>
              <th className="py-2.5 pr-3 text-right font-medium">DTE</th>
              <th className="py-2.5 pr-3 text-right font-medium" title="Net credit taken in at open">
                Credit
              </th>
              <th className="py-2.5 pr-3 text-right font-medium">P&amp;L</th>
              <th
                className="py-2.5 pr-3 text-right font-medium"
                title="Profit or loss as a share of the credit received. +100% means the full credit is captured."
              >
                % of credit
              </th>
              <th className="py-2.5 pr-3 font-medium" style={{ width: 170 }}>
                Position on risk
              </th>
              <th className="py-2.5 pr-3 text-right font-medium" title="Highest |delta| among the short option legs">
                Short Δ
              </th>
              <th className="py-2.5 pr-4 font-medium">Risk</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((v) => {
              const s = v.strategy
              const isOpen = expanded === s.id
              return (
                <Fragment key={s.id}>
                  <tr
                    onClick={() => setExpanded(isOpen ? null : s.id)}
                    className="cursor-pointer border-b border-line/60 transition-colors hover:bg-hover"
                  >
                    <td className="py-2.5 pl-4 pr-3">
                      <div className="font-medium">{s.underlying}</div>
                      {s.roll_count > 0 && (
                        <div className="text-[10px] text-faint">
                          rolled {s.roll_count}×
                        </div>
                      )}
                    </td>
                    <td className="py-2.5 pr-3">
                      <div className="text-ink">{s.strategy_type}</div>
                      <div className="text-[10px] text-faint">
                        {s.risk_profile === 'Defined' ? 'defined risk' : 'undefined risk'}
                      </div>
                    </td>
                    <td className="num py-2.5 pr-3 text-right">
                      <span className={v.risk.dte !== null && v.risk.dte <= 21 ? 'text-tested' : ''}>
                        {dteLabel(v.risk.dte)}
                      </span>
                    </td>
                    <td className="num py-2.5 pr-3 text-right text-muted">{money(s.net_credit, { cents: false })}</td>
                    <td className={`num py-2.5 pr-3 text-right font-medium ${signedClass(v.pnl.open_pnl)}`}>
                      {v.pnl.open_pnl === null ? EM_DASH : money(v.pnl.open_pnl, { sign: true })}
                    </td>
                    <td className={`num py-2.5 pr-3 text-right ${signedClass(v.pnl.pct_of_credit)}`}>
                      {pctOfCredit(v.pnl.pct_of_credit)}
                    </td>
                    <td className="py-2.5 pr-3">
                      <RiskScale pnl={v.pnl} />
                    </td>
                    <td className="num py-2.5 pr-3 text-right text-muted">
                      {decimals(v.risk.worst_short_delta, 2)}
                    </td>
                    <td className="py-2.5 pr-4">
                      <DangerBadge level={v.risk.level} />
                    </td>
                  </tr>

                  {isOpen && (
                    <tr key={`${s.id}-detail`} className="border-b border-line/60 bg-sunken/40">
                      <td colSpan={9} className="px-4 py-3">
                        <div className="grid gap-3 lg:grid-cols-[1fr_360px]">
                          <div className="space-y-3">
                            <LegDetail legs={s.legs} />
                            <PayoffPanel strategyId={s.id} />
                          </div>

                          <div className="rounded-card border border-line bg-raised p-3">
                            <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-faint">
                              Why this risk level
                            </div>
                            {v.risk.reasons.length === 0 ? (
                              <div className="text-xs text-muted">
                                Nothing flagged. The position is inside every threshold.
                              </div>
                            ) : (
                              <ul className="space-y-1.5">
                                {v.risk.reasons.map((r) => (
                                  <li key={r.code} className="flex gap-2 text-xs">
                                    <DangerBadge level={r.level} className="shrink-0" />
                                    <span className="text-muted">{r.message}</span>
                                  </li>
                                ))}
                              </ul>
                            )}

                            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-line pt-3 text-xs">
                              <dt className="text-faint">Max profit</dt>
                              <dd className="num text-right">{money(v.pnl.max_profit, { cents: false })}</dd>
                              <dt className="text-faint">Max loss</dt>
                              <dd className="num text-right">
                                {v.pnl.max_loss === null ? (
                                  <span className="text-muted">undefined</span>
                                ) : (
                                  money(v.pnl.max_loss, { cents: false })
                                )}
                              </dd>
                              <dt className="text-faint">% of max loss</dt>
                              <dd className="num text-right">{pct(v.pnl.pct_of_max_loss, 1)}</dd>
                              <dt className="text-faint">Distance to short</dt>
                              <dd className="num text-right">{pct(v.risk.distance_to_short_pct, 1)}</dd>
                              <dt className="text-faint">In sigma</dt>
                              <dd className="num text-right">
                                {v.risk.distance_to_short_sigma === null
                                  ? EM_DASH
                                  : `${decimals(v.risk.distance_to_short_sigma, 2)}σ`}
                              </dd>
                              <dt className="text-faint">IV rank now</dt>
                              <dd className="num text-right">{pct(v.iv_rank, 0)}</dd>
                            </dl>

                            {v.pnl.quoted_legs < v.pnl.total_legs && (
                              <div className="mt-3 rounded-sm border border-watch/30 bg-watch-soft px-2 py-1.5 text-[11px] text-watch">
                                Only {v.pnl.quoted_legs} of {v.pnl.total_legs} legs are quoted, so P&amp;L is
                                incomplete. Nothing has been guessed.
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
