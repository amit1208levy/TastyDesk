import { Fragment, useState } from 'react'
import { DangerBadge } from './DangerBadge'
import { RiskScale } from './RiskScale'
import { LegDetail } from './LegDetail'
import { PayoffPanel } from './PayoffPanel'
import { money, pct, decimals, EM_DASH } from '../lib/format'
import { formatField, toneClass, type FieldSpec } from '../lib/fields'
import type { StrategyView, DangerLevel } from '../types'
import { DANGER_ORDER } from '../types'

function rank(level: DangerLevel): number {
  return DANGER_ORDER.indexOf(level)
}

/* One cell. Almost every field is printed straight from the catalogue; the
   three that are drawn rather than printed — the ticker with its roll count,
   the risk badge, the bar showing where the position sits on its own risk —
   are named here and nowhere else. */
function Cell({ spec, view, first }: { spec: FieldSpec; view: StrategyView; first: boolean }) {
  const pad = first ? 'py-3.5 pl-4 pr-3' : 'py-3.5 pr-3'
  const align = spec.align === 'right' ? 'text-right' : 'text-left'
  const raw = (view.values ?? {})[spec.id] ?? null

  if (spec.id === 'position_on_risk') {
    return (
      <td className={`${pad}`}>
        <RiskScale pnl={view.pnl} />
      </td>
    )
  }

  if (spec.id === 'risk_level') {
    return (
      <td className={`${pad}`}>
        <DangerBadge level={view.risk.level} />
      </td>
    )
  }

  if (spec.id === 'underlying') {
    return (
      <td className={pad}>
        <div className="text-[17px] font-semibold">{view.strategy.underlying}</div>
        {view.strategy.roll_count > 0 && (
          <div className="text-[12px] text-faint">rolled {view.strategy.roll_count}×</div>
        )}
      </td>
    )
  }

  if (spec.id === 'strategy') {
    return (
      <td className={pad}>
        <div className="text-[17px] font-medium text-ink">
          {view.named_name ?? view.strategy.strategy_type}
        </div>
        <div className="text-[12px] text-faint">
          {view.named_name ? `${view.strategy.strategy_type.toLowerCase()} · ` : ''}
          {view.strategy.risk_profile === 'Defined' ? 'defined risk' : 'undefined risk'}
          {view.parts > 1 ? ` · ${view.parts} trades` : ''}
        </div>
      </td>
    )
  }

  const numeric = spec.format !== 'text' && spec.format !== 'date' && spec.format !== 'level'
  const emphasis =
    spec.id === 'price' || spec.id === 'open_pnl' ? 'text-[17px] font-semibold' : ''

  return (
    <td
      className={`${pad} ${align} ${numeric ? 'figure' : ''} ${emphasis} ${toneClass(spec, raw)}`}
    >
      {formatField(spec, raw)}
    </td>
  )
}

/* The open-positions table. Sorted by danger first, because the whole point of
   the dashboard is that the thing needing attention is at the top, and danger
   here is always the strategy-level assessment. */
export function StrategyTable({
  views,
  columns,
  catalogue,
  legColumns,
  legCatalogue,
}: {
  views: StrategyView[]
  columns: FieldSpec[]
  catalogue: FieldSpec[]
  legColumns: FieldSpec[]
  legCatalogue: FieldSpec[]
}) {
  const [expanded, setExpanded] = useState<string | null>(null)
  void catalogue
  void legCatalogue

  const sorted = [...views].sort((a, b) => {
    const d = rank(b.risk.level) - rank(a.risk.level)
    if (d !== 0) return d
    return b.risk.score - a.risk.score
  })

  if (sorted.length === 0) {
    return (
      <div className="rounded-card border border-dashed border-line bg-raised px-6 py-12 text-center">
        <div className="text-[16px] text-muted">No open strategies.</div>
        <div className="mt-1 text-[14px] text-faint">
          Positions appear here once a sync has run against your tastytrade account.
        </div>
      </div>
    )
  }

  return (
    <div className="sheened overflow-hidden rounded-card border border-line bg-raised shadow-[var(--shadow-md)]">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1000px] text-[16px]">
          <thead>
            <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
              {columns.map((c, i) => (
                <th
                  key={c.id}
                  title={c.hint}
                  style={c.width ? { width: c.width } : undefined}
                  className={`py-3.5 font-medium ${i === 0 ? 'pl-4 pr-3' : 'pr-3'} ${
                    c.align === 'right' ? 'text-right' : 'text-left'
                  }`}
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="rows stagger">
            {sorted.map((v) => {
              const s = v.strategy
              const isOpen = expanded === s.id
              return (
                <Fragment key={s.id}>
                  <tr
                    onClick={() => setExpanded(isOpen ? null : s.id)}
                    className="cursor-pointer border-b border-line/60 transition-colors hover:bg-hover"
                  >
                    {columns.map((c, i) => (
                      <Cell key={c.id} spec={c} view={v} first={i === 0} />
                    ))}
                  </tr>

                  {isOpen && (
                    <tr key={`${s.id}-detail`} className="border-b border-line/60 bg-sunken/40">
                      <td colSpan={columns.length} className="px-4 py-3">
                        <div className="grid gap-3 lg:grid-cols-[1fr_360px]">
                          <div className="space-y-3">
                            <LegDetail view={v} columns={legColumns} />
                            <PayoffPanel strategyId={s.id} />
                          </div>

                          <div className="rounded-card border border-line bg-raised p-3">
                            <div className="mb-2 text-[13px] font-medium uppercase tracking-wider text-faint">
                              Why this risk level
                            </div>
                            {v.risk.reasons.length === 0 ? (
                              <div className="text-[14px] text-muted">
                                Nothing flagged. The position is inside every threshold.
                              </div>
                            ) : (
                              <ul className="space-y-1.5">
                                {v.risk.reasons.map((r) => (
                                  <li key={r.code} className="flex gap-2 text-[14px]">
                                    <DangerBadge level={r.level} className="shrink-0" />
                                    <span className="text-muted">{r.message}</span>
                                  </li>
                                ))}
                              </ul>
                            )}

                            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-line pt-3 text-[14px]">
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
                              <div className="mt-3 rounded-sm border border-watch/30 bg-watch-soft px-2 py-3.5 text-[13px] text-watch">
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
