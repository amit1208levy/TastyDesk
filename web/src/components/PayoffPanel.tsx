import { PayoffChart } from './PayoffChart'
import { Help } from './Help'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'

const HELP =
  'What this structure would be worth at every price the underlying could finish at, on the day ' +
  'it expires. The shaded area above the line is profit and below it is loss, the dashed lines ' +
  'are your strikes, the brass line is where the underlying is trading now, and the hollow dot ' +
  'on the zero line is your breakeven. Hover anywhere on it to read the price and what the trade ' +
  'settles at there. It is the shape of the risk, not a forecast: nothing here says how likely ' +
  'any of those prices are.'

const HELP_INEXACT =
  ' These legs do not all expire on the same day, so the line settles them together — which the ' +
  'later leg will not do. Read it as the shape of the position, not as its value.'

/* Loaded per strategy and only when the row is expanded — there is no point
   computing a payoff curve for forty positions the user is not looking at. */
export function PayoffPanel({ strategyId }: { strategyId: string }) {
  const { data, error, loading } = useAsync(() => api.payoff(strategyId), [strategyId])
  const exact = data?.exact !== false

  return (
    <div className="rounded-card border border-line bg-sunken p-3">
      <div className="mb-1.5">
        <Help
          title={exact ? 'At expiration' : 'If every leg expired together'}
          body={HELP + (exact ? '' : HELP_INEXACT)}
        >
          <span className="text-[13px] font-medium uppercase tracking-wider text-muted">
            {exact ? 'At expiration' : 'If every leg expired together'}
          </span>
        </Help>
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
