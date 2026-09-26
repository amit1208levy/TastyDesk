import { useState } from 'react'
import { FieldPicker } from './FieldPicker'
import { Help } from './Help'
import { formatField, toneClass, type FieldSpec } from '../lib/fields'
import type { StrategyView } from '../types'

/* Per-leg numbers, drawn from the same template every time a leg appears, and
   labelled — unmissably — as detail rather than signal.

   A short put inside a credit spread routinely shows a far worse percentage
   move than the spread it belongs to, because the long put gained at the very
   same moment. That number is real but it is not risk, and every alarm in this
   application is computed one level up, on the strategy.

   Which columns appear is the user's choice; the order is his too. What is
   fixed is that a leg reads the same way everywhere.

   The control for that choice used to live only at the top of the page, three
   screens away from the legs it governs and named "Customise" beside the
   position columns, so the fact that it also drove this table was something
   you had to already know. It is here now, on the panel it changes. */
export function LegDetail({
  view,
  columns,
  catalogue,
  onColumns,
}: {
  view: StrategyView
  columns: FieldSpec[]
  catalogue?: FieldSpec[]
  onColumns?: (ids: string[]) => void | Promise<void>
}) {
  const rows = view.leg_values ?? []
  const [picking, setPicking] = useState(false)

  return (
    <div className="rounded-card border border-line bg-sunken p-4">
      <div className="mb-2.5 flex items-baseline gap-3">
        <Help
          title="Legs"
          body={
            'The contracts this position is made of, drawn from the same template every time a leg ' +
            'appears so a leg always reads the same way. This is detail, not signal: a short put ' +
            'inside a credit spread routinely shows a far worse percentage move than the spread it ' +
            'belongs to, because the long put gained at the same moment. Every alarm in this app is ' +
            'computed one level up, on the whole structure. Hover any column heading to see what it ' +
            'measures, and use Customise to change which ones appear.'
          }
        >
          <span className="label text-[13px]">Legs</span>
        </Help>
        {catalogue && onColumns && (
          <button
            onClick={() => setPicking(!picking)}
            className="ml-auto rounded-sm border border-line px-2.5 py-1 text-[13px] text-muted transition-colors hover:bg-hover hover:text-ink"
          >
            {picking ? 'Done' : 'Customise'}
          </button>
        )}
      </div>

      {picking && catalogue && onColumns && (
        <div className="mb-3">
          <FieldPicker
            title="What every leg shows"
            catalogue={catalogue}
            chosen={columns.map((c) => c.id)}
            onChange={(ids) => void onColumns(ids)}
            onClose={() => setPicking(false)}
          />
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-[15px]">
          <thead>
            <tr className="text-left text-[13px] uppercase tracking-wider text-muted">
              {columns.map((c, i) => (
                <th
                  key={c.id}
                  className={`pb-2 font-medium ${i === 0 ? 'pr-3' : 'pr-3'} ${
                    c.align === 'right' ? 'text-right' : 'text-left'
                  }`}
                >
                  <Help title={c.label} body={c.help}>
                    <span>{c.label}</span>
                  </Help>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((values, i) => (
              <tr key={i} className="border-t border-line/70">
                {columns.map((c) => {
                  const raw = values[c.id] ?? null
                  const numeric =
                    c.format !== 'text' && c.format !== 'date' && c.format !== 'level'
                  if (c.id === 'leg') {
                    const short = String(values.side ?? '') === 'short'
                    return (
                      <td key={c.id} className="whitespace-nowrap py-2.5 pr-3">
                        <span
                          className={`mr-2 inline-block w-12 rounded px-1 py-0.5 text-center text-[12px] uppercase tracking-wide ${
                            short ? 'bg-accent-soft text-accent' : 'bg-sunken text-muted'
                          }`}
                        >
                          {short ? 'short' : 'long'}
                        </span>
                        {String(values.strike ?? '') === ''
                          ? String(values.right ?? '')
                          : `${values.strike} ${values.right}`}
                      </td>
                    )
                  }
                  return (
                    <td
                      key={c.id}
                      className={`py-2.5 pr-3 ${c.align === 'right' ? 'text-right' : 'text-left'} ${
                        numeric ? 'figure' : 'text-muted'
                      } ${toneClass(c, raw)}`}
                    >
                      {formatField(c, raw)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
