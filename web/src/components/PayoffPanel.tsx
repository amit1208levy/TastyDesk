import { PayoffChart } from './PayoffChart'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'

/* Loaded per strategy and only when the row is expanded — there is no point
   computing a payoff curve for forty positions the user is not looking at. */
export function PayoffPanel({ strategyId }: { strategyId: string }) {
  const { data, error, loading } = useAsync(() => api.payoff(strategyId), [strategyId])

  return (
    <div className="rounded-card border border-line bg-sunken/60 p-3">
      <div className="mb-1.5 flex items-baseline gap-2">
        <span className="text-[13px] font-medium uppercase tracking-wider text-faint">At expiration</span>
        <span className="text-[13px] text-faint">where this structure makes and loses money</span>
      </div>
      {loading && !data ? (
        <div className="py-6 text-[14px] text-faint">Loading…</div>
      ) : error || !data ? (
        <div className="py-6 text-[14px] text-faint">No payoff available for this structure.</div>
      ) : (
        <PayoffChart curve={data} />
      )}
    </div>
  )
}
