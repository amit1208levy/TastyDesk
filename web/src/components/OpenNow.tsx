import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { EM_DASH, money, num, pct, shortDate } from '../lib/format'

/* What is open right now, and what each position is making or losing, on the
   page that otherwise only counts what has closed. Folded by default: the
   page is about the record, and this is the part not yet in it. */
export function OpenNow() {
  const [open, setOpen] = useState(false)
  const views = useAsync(() => api.openStrategies(), [], 60_000)
  const rows = [...(views.data ?? [])].sort(
    (a, b) => (num(a.pnl.open_pnl) ?? 0) - (num(b.pnl.open_pnl) ?? 0),
  )
  const priced = rows.filter((v) => num(v.pnl.open_pnl) !== null)
  const total = priced.reduce((acc, v) => acc + (num(v.pnl.open_pnl) ?? 0), 0)
  const today = rows.reduce((acc, v) => acc + (num(v.values.day_change as string | null) ?? 0), 0)
  const tone = (n: number | null) => (n === null ? 'text-faint' : n >= 0 ? 'text-profit' : 'text-loss')

  return (
    <section className="sheened rounded-card border border-line bg-raised p-4 shadow-[var(--shadow-sm)]">
      <button onClick={() => setOpen(!open)} className="flex w-full flex-wrap items-baseline gap-x-4 gap-y-1 text-left">
        <span className="text-[16px] font-semibold">Open now · {rows.length}</span>
        <span className={`figure text-[18px] font-semibold ${tone(total)}`}>
          {money(total, { sign: true, cents: false })}
        </span>
        <span className="text-[14px] text-muted">
          open P&amp;L, not banked · today{' '}
          <span className={tone(today)}>{money(today, { sign: true, cents: false })}</span>
          {priced.length < rows.length && ` · ${rows.length - priced.length} not priced right now`}
        </span>
        <span className="ml-auto text-[14px] text-muted">{open ? 'Hide ▴' : 'Show positions ▾'}</span>
      </button>

      {open && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-[15px]">
            <thead>
              <tr className="text-left text-[12px] uppercase tracking-wider text-muted">
                <th className="py-2 pr-3 font-medium">Position</th>
                <th className="py-2 pr-3 font-medium">Opened</th>
                <th className="py-2 pr-3 text-right font-medium">Took in / paid</th>
                <th className="py-2 pr-3 text-right font-medium">P&amp;L</th>
                <th className="py-2 pr-3 text-right font-medium">% of credit</th>
                <th className="py-2 text-right font-medium">Today</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((v) => {
                const pnl = num(v.pnl.open_pnl)
                const credit = num(v.pnl.net_credit)
                const day = num(v.values.day_change as string | null)
                const held = Math.max(
                  0,
                  Math.round((Date.now() - new Date(v.strategy.opened_at).getTime()) / 86_400_000),
                )
                return (
                  <tr key={v.strategy.id} className="border-t border-line align-baseline">
                    <td className="w-full py-2 pr-3">
                      <span className="font-medium">{v.named_name ?? v.strategy.underlying}</span>{' '}
                      <span className="text-muted">
                        {v.strategy.underlying} · {v.strategy.strategy_type}
                      </span>
                    </td>
                    <td className="whitespace-nowrap py-2 pr-3 text-muted">
                      {shortDate(v.strategy.opened_at)} · {held}d
                    </td>
                    <td className="num whitespace-nowrap py-2 pr-3 text-right">
                      {credit === null
                        ? EM_DASH
                        : `${money(Math.abs(credit), { cents: false })} ${credit >= 0 ? 'in' : 'paid'}`}
                    </td>
                    <td className={`num whitespace-nowrap py-2 pr-3 text-right font-medium ${tone(pnl)}`}>
                      {pnl === null ? 'not quoted' : money(pnl, { sign: true, cents: false })}
                    </td>
                    <td className="num whitespace-nowrap py-2 pr-3 text-right text-muted">
                      {v.pnl.pct_of_credit === null ? EM_DASH : pct(v.pnl.pct_of_credit, 0, true)}
                    </td>
                    <td className={`num whitespace-nowrap py-2 text-right ${tone(day)}`}>
                      {day === null ? EM_DASH : money(day, { sign: true, cents: false })}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
