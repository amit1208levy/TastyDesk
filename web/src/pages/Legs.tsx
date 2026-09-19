import { useMemo, useState } from 'react'
import { ErrorPanel, Loading, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, decimals, dteLabel, num, EM_DASH } from '../lib/format'
import type { OpenLeg } from '../types'

/* Every open leg on its own line.

   The broker's order grouping is not the same thing as a strategy, and the user
   asked to be the one who decides. So nothing is combined: pick the legs that
   belong together, give the group a name, and that becomes the unit everything
   downstream is measured on.

   Legs that already belong to a named strategy sink to the bottom of the table.
   What is left at the top is the work still to do. */
export function Legs() {
  const legs = useAsync(() => api.openLegs(), [], 30_000)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const rows = legs.data ?? []

  const loose = useMemo(() => rows.filter((r) => r.in_strategies.length === 0), [rows])

  const grouped = useMemo(() => {
    const inside = rows.filter((r) => r.in_strategies.length > 0)
    return inside.sort((a, b) => {
      const an = a.in_strategies[0].name
      const bn = b.in_strategies[0].name
      if (an !== bn) return an.localeCompare(bn)
      return a.underlying.localeCompare(b.underlying)
    })
  }, [rows])

  const chosenTrades = useMemo(() => {
    const ids = new Set<string>()
    for (const r of rows) if (picked.has(r.leg_id)) ids.add(r.trade_id)
    return Array.from(ids)
  }, [picked, rows])

  const products = useMemo(() => {
    const p = new Set<string>()
    for (const r of rows) if (picked.has(r.leg_id)) p.add(r.product)
    return Array.from(p)
  }, [picked, rows])

  if (legs.error) return <ErrorPanel error={legs.error} onRetry={legs.reload} />
  if (!legs.data) return <Loading label="Reading your legs" />

  function toggle(id: string) {
    setPicked((old) => {
      const next = new Set(old)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function group() {
    if (!name.trim() || chosenTrades.length === 0) return
    setSaving(true)
    setProblem(null)
    try {
      await api.createNamedStrategy(name.trim(), chosenTrades)
      setPicked(new Set())
      setName('')
      legs.reload()
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const tooManyProducts = products.length > 1

  function Row({ r, faded }: { r: OpenLeg; faded: boolean }) {
    const on = picked.has(r.leg_id)
    return (
      <tr
        onClick={() => toggle(r.leg_id)}
        className={`cursor-pointer border-b border-line/60 transition-colors ${
          on ? 'bg-accent-soft' : faded ? 'opacity-55 hover:bg-hover hover:opacity-100' : 'hover:bg-hover'
        }`}
      >
        <td className="py-3 pl-4">
          <input type="checkbox" checked={on} readOnly className="pointer-events-none accent-current" />
        </td>
        <td className="py-3 pr-3 font-medium">{r.underlying}</td>
        <td className="py-3 pr-3">
          <span
            className={`mr-1.5 inline-block w-9 rounded px-1 text-center text-[12px] uppercase ${
              r.side === 'Short' ? 'bg-sunken text-accent' : 'bg-sunken text-muted'
            }`}
          >
            {r.side === 'Short' ? 'short' : 'long'}
          </span>
          {r.right === 'shares' ? 'shares' : `${r.strike ?? ''} ${r.right === 'C' ? 'call' : 'put'}`}
        </td>
        <td className="num py-3 pr-3 text-right">{decimals(r.quantity, 0)}</td>
        <td className="num py-3 pr-3 text-right">{dteLabel(r.dte)}</td>
        <td className="num py-3 pr-3 text-right text-muted">{money(r.open_price)}</td>
        <td className="num py-3 pr-3 text-right">{r.mark === null ? EM_DASH : money(r.mark)}</td>
        <td className="num py-3 pr-3 text-right text-muted">{decimals(r.delta, 2)}</td>
        <td className="py-3 pr-4">
          {r.in_strategies.length === 0 ? (
            <span className="text-[13px] text-faint">—</span>
          ) : (
            <span className="flex flex-wrap gap-1">
              {r.in_strategies.map((s) => (
                <span
                  key={s.id}
                  className="rounded-full border border-line px-1.5 py-0.5 text-[12px] text-muted"
                >
                  {s.name}
                </span>
              ))}
            </span>
          )}
        </td>
      </tr>
    )
  }

  return (
    <div className="space-y-4">
      <SectionHeading
        title="Open legs"
        hint="ungrouped first — anything you have named drops to the bottom"
      />

      {picked.size > 0 && (
        <div className="sticky top-14 z-10 rounded-card border border-accent/40 bg-accent-soft px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[16px] font-medium text-accent">
              {picked.size} leg{picked.size === 1 ? '' : 's'} selected
            </span>
            {tooManyProducts ? (
              <span className="text-[14px] text-loss">
                Those span {products.join(', ')} — a strategy has to be one ticker.
              </span>
            ) : (
              <span className="text-[14px] text-muted">on {products[0]}</span>
            )}
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void group()
              }}
              placeholder="Name this strategy…"
              className="ml-auto w-56 rounded-sm border border-line bg-bg px-2 py-1 text-[16px] outline-none placeholder:text-faint focus:border-accent"
            />
            <button
              onClick={() => void group()}
              disabled={saving || !name.trim() || tooManyProducts}
              className="rounded-sm border border-accent/50 bg-bg px-3 py-1 text-[14px] text-accent transition-colors hover:bg-accent/10 disabled:opacity-40"
            >
              {saving ? 'Saving…' : 'Group and name'}
            </button>
            <button
              onClick={() => setPicked(new Set())}
              className="rounded-sm px-2 py-1 text-[14px] text-muted hover:text-ink"
            >
              Clear
            </button>
          </div>
          {problem && <div className="mt-1.5 text-[14px] text-loss">{problem}</div>}
        </div>
      )}

      <div className="overflow-hidden rounded-card border border-line bg-raised">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] text-[16px]">
            <thead>
              <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
                <th className="w-8 py-3.5 pl-4" />
                <th className="py-3.5 pr-3 font-medium">Underlying</th>
                <th className="py-3.5 pr-3 font-medium">Leg</th>
                <th className="py-3.5 pr-3 text-right font-medium">Qty</th>
                <th className="py-3.5 pr-3 text-right font-medium">DTE</th>
                <th className="py-3.5 pr-3 text-right font-medium">Open</th>
                <th className="py-3.5 pr-3 text-right font-medium">Mark</th>
                <th className="py-3.5 pr-3 text-right font-medium">Delta</th>
                <th className="py-3.5 pr-4 font-medium">In strategy</th>
              </tr>
            </thead>
            <tbody>
              {loose.length === 0 && grouped.length > 0 && (
                <tr>
                  <td colSpan={9} className="bg-sunken/60 px-4 py-3 text-[13px] text-muted">
                    Every open leg is in a strategy. Nothing left to name.
                  </td>
                </tr>
              )}
              {loose.map((r) => (
                <Row key={r.leg_id} r={r} faded={false} />
              ))}

              {grouped.length > 0 && (
                <tr>
                  <td
                    colSpan={9}
                    className="border-y border-line bg-sunken/60 px-4 py-3.5 text-[12px] uppercase tracking-wider text-faint"
                  >
                    Already grouped — {num(grouped.length)} leg{grouped.length === 1 ? '' : 's'}
                  </td>
                </tr>
              )}
              {grouped.map((r) => (
                <Row key={r.leg_id} r={r} faded />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-[13px] text-faint">
        {num(rows.length)} legs, {num(loose.length)} still ungrouped. Pick the ones that belong to
        one idea, name it, and the app will look back through your history for trades shaped the
        same way.
      </p>
    </div>
  )
}
