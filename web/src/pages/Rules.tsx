import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, num, EM_DASH } from '../lib/format'
import type { RuleAdherence } from '../types'

/* Rule adherence is the closest thing to an honest answer to "how do I improve".

   It deliberately counts a winner held past the 50% target as a violation.
   Ending green does not make it the trade the plan called for, and grading only
   on outcome is how a trader learns the wrong lesson from a lucky hold. */

function AdherenceBar({ followed, violated, unmeasurable }: { followed: number; violated: number; unmeasurable: number }) {
  const total = followed + violated + unmeasurable
  if (total === 0) return null
  const w = (n: number) => `${(n / total) * 100}%`
  return (
    <div className="flex h-2.5 w-full gap-[2px] overflow-hidden rounded-full">
      {followed > 0 && <div className="bg-chart-profit" style={{ width: w(followed) }} title={`${followed} followed`} />}
      {violated > 0 && <div className="bg-chart-loss" style={{ width: w(violated) }} title={`${violated} violated`} />}
      {unmeasurable > 0 && (
        <div className="bg-line-strong" style={{ width: w(unmeasurable) }} title={`${unmeasurable} not measurable`} />
      )}
    </div>
  )
}

/* The engine keys rules by identifier; a person reading the page wants the name
   of the rule, not the name of the field. */
const RULE_TITLES: Record<string, string> = {
  profit_target: 'Manage at 50%',
  dte_exit: 'Close or roll at 21 DTE',
  stop_loss: 'Stop at 2× credit',
}

function ruleTitle(key: string): string {
  return RULE_TITLES[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

function RuleCard({ rule }: { rule: RuleAdherence }) {
  const measurable = rule.followed + rule.violated
  const delta =
    rule.pnl_when_followed !== null && rule.pnl_when_violated !== null
      ? (num(rule.pnl_when_followed) ?? 0) - (num(rule.pnl_when_violated) ?? 0)
      : null

  return (
    <div className="sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)] p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[16px] font-semibold">{ruleTitle(rule.rule)}</h3>
        <span className="num text-[21px] font-semibold">
          {rule.adherence_rate === null ? (
            <span className="text-faint">{EM_DASH}</span>
          ) : (
            pct(rule.adherence_rate, 0)
          )}
        </span>
      </div>
      <p className="mt-0.5 text-[14px] text-muted">{rule.description}</p>

      <div className="mt-3">
        <AdherenceBar followed={rule.followed} violated={rule.violated} unmeasurable={rule.not_measurable} />
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[13px]">
          <span className="text-profit">{rule.followed} followed</span>
          <span className="text-loss">{rule.violated} violated</span>
          {rule.not_measurable > 0 && (
            <span className="text-faint" title="Not enough recorded history to judge these">
              {rule.not_measurable} not measurable
            </span>
          )}
        </div>
      </div>

      {measurable > 0 && (
        <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-line pt-3 text-[14px]">
          <dt className="text-faint">P&amp;L when followed</dt>
          <dd className="num text-right text-profit">{money(rule.pnl_when_followed, { sign: true, cents: false })}</dd>
          <dt className="text-faint">P&amp;L when broken</dt>
          <dd className="num text-right text-loss">{money(rule.pnl_when_violated, { sign: true, cents: false })}</dd>
          {rule.counterfactual_pnl !== null && (
            <>
              <dt className="text-faint" title="What the P&L would have been had the rule been applied mechanically">
                If followed every time
              </dt>
              <dd className="num text-right">{money(rule.counterfactual_pnl, { sign: true, cents: false })}</dd>
            </>
          )}
        </dl>
      )}

      {rule.counterfactual_excluded > 0 && (
        <p className="mt-2 text-[13px] text-faint">
          {rule.counterfactual_excluded} trade{rule.counterfactual_excluded === 1 ? '' : 's'} could not be
          modelled and {rule.counterfactual_excluded === 1 ? 'is' : 'are'} excluded from that figure.
        </p>
      )}

      {delta !== null && measurable >= 5 && (
        <p className="mt-2 text-[13px] text-muted">
          {delta > 0
            ? `Following this rule has been worth ${money(delta, { cents: false })} more than breaking it.`
            : `Breaking this rule has not cost you money so far — on ${measurable} trades, which is not many.`}
        </p>
      )}
    </div>
  )
}

export function Rules() {
  const { data, error, loading, reload } = useAsync(() => api.rules(), [])

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Grading your trades against your rules" />

  const rules: RuleAdherence[] = Array.isArray(data) ? data : Object.values(data ?? {})
  if (rules.length === 0) {
    return <Empty title="No closed trades to grade yet." hint="Run a sync, then come back once trades have closed." />
  }

  return (
    <div className="space-y-5">
      <section>
        <SectionHeading
          title="Your rules"
          hint="manage at 50% of max profit · close or roll at 21 DTE · stop at 2× credit"
        />
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rules.map((r) => (
            <RuleCard key={r.rule} rule={r} />
          ))}
        </div>
      </section>

      <section className="sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)] px-4 py-3">
        <h3 className="text-[13px] font-medium uppercase tracking-wider text-faint">How this is graded</h3>
        <ul className="mt-1.5 space-y-1 text-[14px] text-muted">
          <li>
            A <strong className="font-medium text-ink">winner held past 50%</strong> of max profit counts as a
            violation. Ending green does not make it the trade the plan called for.
          </li>
          <li>
            The 2× stop needs daily snapshots to know how bad a trade got at its worst. Trades from before
            snapshots began are counted as <em>not measurable</em> rather than guessed at.
          </li>
          <li>
            Counterfactuals exclude any trade whose outcome under the rule cannot be reconstructed, and the
            count of exclusions is shown rather than buried.
          </li>
        </ul>
      </section>
    </div>
  )
}
