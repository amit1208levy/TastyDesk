import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, fullDate } from '../lib/format'

/* Trades the journal could not settle: the options expired and no closing
   transaction ever arrived, so the recorded cash flows may be missing an
   exercise. They are kept out of every total, which is exactly why they have
   to be visible — a total that silently omits trades is worse than one that
   says which. */
export function NeedsReview() {
  const { data } = useAsync(() => api.needsReview(), [])
  const [open, setOpen] = useState(false)

  if (!data || data.length === 0) return null

  return (
    <div className="rounded-card border border-watch/40 bg-watch-soft/40 p-4">
      <button onClick={() => setOpen(!open)} className="flex w-full items-baseline gap-2 text-left">
        <h3 className="text-[13px] font-semibold text-watch">
          {data.length} trade{data.length === 1 ? '' : 's'} could not be settled
        </h3>
        <span className="text-[11px] text-muted">
          excluded from every total until confirmed
        </span>
        <span className="ml-auto text-[11px] text-muted">{open ? 'Hide' : 'Show'}</span>
      </button>

      <p className="mt-1 text-xs text-muted">
        The options expired and no closing transaction arrived, so the recorded cash flows may be
        missing an exercise or assignment. Counting them would have moved your realized figure by
        four times its true value.
      </p>

      {open && (
        <ul className="mt-3 space-y-2">
          {data.map((t) => (
            <li key={t.id} className="border-t border-watch/25 pt-2 text-xs">
              <div className="flex items-baseline gap-2">
                <span className="font-medium">{t.underlying}</span>
                <span className="text-muted">{t.structure}</span>
                <span className="text-faint">expired {fullDate(t.closed)}</span>
                <span className="num ml-auto text-muted">recorded {money(t.recorded_pnl)}</span>
              </div>
              <ul className="mono mt-1 space-y-0.5 text-[10px] text-faint">
                {t.legs.map((leg) => (
                  <li key={leg}>{leg}</li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
