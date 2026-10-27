import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { num } from '../lib/format'
import { ErrorPanel, Loading, SectionHeading } from '../components/States'
import { Allocation } from './Tom'
import { WhatIf } from './WhatIf'

/* Where the buying power sits and where it could go, on one page: Tom's
   allocation, with each strategy placed in a part of the plan (by the app's
   guess, or by hand), and What if under it to move price, volatility and time. */
export function AllocationPage() {
  const tom = useAsync(() => api.tom(), [], 60_000)
  const settings = useAsync(() => api.settings(), [])

  async function move(id: string, part: string | null) {
    const next = { ...(settings.data?.bucket_overrides ?? {}) }
    if (part === null) delete next[id]
    else next[id] = part
    await api.setSetting('bucket_overrides', JSON.stringify(next))
    settings.reload()
    tom.reload()
  }

  return (
    <div className="space-y-10">
      <section>
        <SectionHeading
          title="Allocation"
          hint="where your buying power sits, against what the plan allows"
          help="Open a line to see the strategies in it. 'Part of' moves a strategy to another part of the plan; 'Automatic' puts it back to the app's guess."
        />
        {tom.error && !tom.data ? (
          <ErrorPanel error={tom.error} onRetry={tom.reload} />
        ) : !tom.data ? (
          <Loading label="Reading your book" />
        ) : (
          <Allocation
            total={tom.data.allocation_total}
            rows={tom.data.strategy_bp}
            positions={tom.data.positions}
            netLiq={num(tom.data.net_liq)}
            onMove={move}
          />
        )}
      </section>
      <section>
        <WhatIf />
      </section>
    </div>
  )
}
