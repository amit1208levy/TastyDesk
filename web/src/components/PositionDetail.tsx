import type { ReactNode } from 'react'
import { DangerBadge } from './DangerBadge'
import { ExpectedRange } from './ExpectedRange'
import { LegDetail } from './LegDetail'
import { PayoffPanel } from './PayoffPanel'
import { PlanLines } from './TradePlan'
import { decimals, EM_DASH, money, num, pct } from '../lib/format'
import type { FieldSpec } from '../lib/fields'
import type { StrategyView } from '../types'

function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[14px] text-muted">{label}</dt>
      <dd className="num mt-0.5 text-[17px] text-ink">{children}</dd>
    </div>
  )
}

/* Everything about one open position: its legs, why it is at the risk level
   it is, where the market can take it, and what it pays at expiry. The same
   component on the Positions page and on the Strategies page, so the two can
   never show a position differently. */
export function PositionDetail({
  view: v,
  legColumns,
  legCatalogue,
  onLegColumns,
  showPlan = true,
}: {
  view: StrategyView
  legColumns: FieldSpec[]
  legCatalogue?: FieldSpec[]
  onLegColumns?: (ids: string[]) => void | Promise<void>
  /** Off where the plan is already shown just above. */
  showPlan?: boolean
}) {
  const s = v.strategy
  const calledAway = num(v.pnl.called_away)
  return (
    <div className="space-y-3">
      {/* Full-width rows, not two columns. Side by side,
          the risk panel grew with every reason it had to
          give, and a position with four reasons stood a
          tall narrow panel beside a screen of nothing.
          Rows cannot leave a hole: each one is as tall as
          what is in it. */}
      {showPlan && v.plan && v.plan.length > 0 && (
        <div className="rounded-card border border-line bg-raised p-4">
          <div className="mb-3 text-[14px] font-medium uppercase tracking-wider text-muted">
            Your plan{v.named_name ? ` for ${v.named_name}` : ''}
          </div>
          <PlanLines checks={v.plan} />
        </div>
      )}

      <LegDetail
        view={v}
        columns={legColumns}
        catalogue={legCatalogue}
        onColumns={onLegColumns}
      />

      <div className="rounded-card border border-line bg-raised p-4">
        <div className="mb-3 text-[14px] font-medium uppercase tracking-wider text-muted">
          Why this risk level
        </div>
        {v.risk.reasons.length === 0 ? (
          <div className="text-[16px] text-ink">
            Nothing flagged. The position is inside every threshold.
          </div>
        ) : (
          <ul className="grid gap-x-6 gap-y-2.5 xl:grid-cols-2">
            {v.risk.reasons.map((r) => (
              <li key={r.code} className="flex items-start gap-2.5 text-[16px] leading-relaxed">
                <DangerBadge level={r.level} className="shrink-0" />
                <span className="text-ink">{r.message}</span>
              </li>
            ))}
          </ul>
        )}

        <ExpectedRange view={v} />

        {/* The numbers as a strip of tiles, label over
            figure, so they sit in one line across the
            width instead of a tall two-column list. */}
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4 sm:grid-cols-3 xl:grid-cols-6">
          {/* A covered short has an answer even when max
              profit and max loss do not: assignment sells
              at the short strike, the cover buys at its
              own, and the difference is exact. */}
          {calledAway !== null ? (
            <Tile label="If called away">
              <span className={calledAway >= 0 ? 'text-profit' : 'text-loss'}>
                {money(v.pnl.called_away, { sign: true, cents: false })}
              </span>
            </Tile>
          ) : (
            <>
              <Tile label="Max profit">{money(v.pnl.max_profit, { cents: false })}</Tile>
              <Tile label="Max loss">
                {v.pnl.max_loss !== null ? (
                  money(v.pnl.max_loss, { cents: false })
                ) : s.is_multi_expiration ? (
                  EM_DASH
                ) : (
                  <span className="text-muted">undefined</span>
                )}
              </Tile>
              <Tile label="% of max loss">{pct(v.pnl.pct_of_max_loss, 1)}</Tile>
            </>
          )}
          {/* In the underlying's own units first, because
              the expected move beside it is in those
              units too. */}
          <Tile label="Room to the short">
            {(() => {
              const away = num(v.risk.distance_to_short_pct)
              const price = num(v.underlying_price)
              return away === null
                ? EM_DASH
                : price === null
                  ? pct(away, 1)
                  : `${decimals(away * price, 2)} (${pct(away, 1)})`
            })()}
          </Tile>
          <Tile label="Expected move">
            {v.risk.expected_move === null
              ? EM_DASH
              : `±${decimals(v.risk.expected_move, 2)}`}
          </Tile>
          <Tile label="IV rank now">{pct(v.iv_rank, 0)}</Tile>
        </dl>

        {/* A refusal with a reason, and only where the
            refusal is all there is to say. */}
        {s.is_multi_expiration && calledAway === null && (
          <p className="mt-3 text-[15px] leading-relaxed text-tested">
            These legs expire on different days. While the later one still has
            time value, max profit and max loss cannot be worked out from the
            strikes, so they are left blank rather than guessed.
          </p>
        )}

        {v.pnl.quoted_legs < v.pnl.total_legs && (
          <div className="mt-3 rounded-sm border border-watch/30 bg-watch-soft px-2.5 py-3 text-[15px] leading-relaxed text-watch">
            Only {v.pnl.quoted_legs} of {v.pnl.total_legs} legs are quoted, so P&amp;L is
            incomplete. Nothing has been guessed.
          </div>
        )}
      </div>

      <PayoffPanel strategyId={s.id} />
    </div>
  )
}
