import { useCallback, useEffect, useRef, useState } from 'react'
import { FieldPicker } from './FieldPicker'
import { Estimate } from './Estimate'
import { Help } from './Help'
import { formatField, summariseRows, toneClass, type FieldSpec } from '../lib/fields'
import { useLegMover } from './legMove'
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
  columns: chosen,
  catalogue,
  onColumns,
}: {
  view: StrategyView
  columns: FieldSpec[]
  catalogue?: FieldSpec[]
  onColumns?: (ids: string[]) => void | Promise<void>
}) {
  const rows = view.leg_values ?? []
  const mover = useLegMover()
  const fromId = view.strategy.id
  // Same rule as the table above: a column empty on every leg of this position
  // is dropped rather than printed as a row of dashes. It is per position, so
  // a strike column disappears on a futures-only trade and stays everywhere
  // else.
  const columns = chosen.filter((c) =>
    rows.some((r) => r[c.id] !== null && r[c.id] !== undefined),
  )
  const [picking, setPicking] = useState(false)

  /* Turning the page on the legs, the way the positions table does.

     With more than a handful of columns chosen this table runs off its panel,
     and the only way to see the rest was to find a scrollbar inside a drawer
     inside a table. The button does it instead, landing on a column boundary
     rather than slicing one, and says how many are still out of sight. */
  const scroller = useRef<HTMLDivElement>(null)
  const [hiddenCols, setHiddenCols] = useState(0)
  const [atStart, setAtStart] = useState(true)

  const measure = useCallback(() => {
    const box = scroller.current
    if (!box) return
    const edge = box.getBoundingClientRect().right
    const heads = Array.from(box.querySelectorAll('thead th:not([data-filler])'))
    setHiddenCols(heads.filter((th) => th.getBoundingClientRect().right > edge + 1).length)
    setAtStart(box.scrollLeft < 8)
  }, [])

  useEffect(() => {
    measure()
    const box = scroller.current
    if (!box) return
    box.addEventListener('scroll', measure, { passive: true })
    window.addEventListener('resize', measure)
    return () => {
      box.removeEventListener('scroll', measure)
      window.removeEventListener('resize', measure)
    }
  }, [measure, columns.length, rows.length, picking])

  function slide() {
    const box = scroller.current
    if (!box) return
    const heads = Array.from(box.querySelectorAll<HTMLElement>('thead th:not([data-filler])'))
    const frame = box.getBoundingClientRect()
    const limit = box.scrollWidth - box.clientWidth
    const cut = heads.find((th) => th.getBoundingClientRect().right > frame.right - 1)
    const to =
      !cut || box.scrollLeft >= limit - 2
        ? 0
        : Math.min(limit, box.scrollLeft + (cut.getBoundingClientRect().left - frame.left))
    box.scrollLeft = to
    measure()
  }

  return (
    <div className="rounded-card border border-line bg-sunken p-4">
      <div className="mb-2.5 flex items-baseline gap-3">
        <Help
          title="Open legs"
          body={
            'The contracts this position is made of, drawn from the same template every time a leg ' +
            'appears so a leg always reads the same way. This is detail, not signal: a short put ' +
            'inside a credit spread routinely shows a far worse percentage move than the spread it ' +
            'belongs to, because the long put gained at the same moment. Every alarm in this app is ' +
            'computed one level up, on the whole structure. Hover any column heading to see what it ' +
            'measures, and use Customise to change which ones appear.'
          }
        >
          <span className="label text-note">Open legs</span>
        </Help>
        {(hiddenCols > 0 || !atStart) && (
          <button
            onClick={slide}
            className={`ml-auto rounded-sm border px-2.5 py-1 text-note transition-colors ${
              hiddenCols > 0
                ? 'border-line text-muted hover:bg-hover hover:text-ink'
                : 'border-accent/50 bg-accent-soft text-accent'
            }`}
          >
            {hiddenCols > 0
              ? `${hiddenCols} more →`
              : '← back to the start'}
          </button>
        )}
        {catalogue && onColumns && (
          <button
            onClick={() => setPicking(!picking)}
            className={`rounded-sm border border-line px-2.5 py-1 text-note text-muted transition-colors hover:bg-hover hover:text-ink ${
              hiddenCols > 0 || !atStart ? '' : 'ml-auto'
            }`}
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

      {/* The fade says the row continues; the button says how far. */}
      <div className="relative">
        {hiddenCols > 0 && (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 z-10 w-10"
            style={{ background: 'linear-gradient(to right, transparent, var(--bg-sunken))' }}
          />
        )}
      <div ref={scroller} className="overflow-x-auto">
        <table className="w-max min-w-full text-copy">
          <thead>
            <tr className="text-left text-note uppercase tracking-wider text-muted">
              {mover && <th aria-hidden className="w-0 pb-2 pr-3" />}
              {columns.map((c) => (
                <th
                  key={c.id}
                  className={`whitespace-nowrap pb-2 font-medium pr-7 ${
                    c.align === 'right' ? 'text-right' : 'text-left'
                  }`}
                >
                  <Help title={c.label} body={c.help}>
                    <span>{c.label}</span>
                  </Help>
                </th>
              ))}
              {/* Takes whatever width is left, so the rows reach the edge of the panel
                  instead of stopping short of it, without spreading the figures apart. */}
              <th aria-hidden data-filler className="w-full p-0" />
            </tr>
            {/* What the legs come to. A position is read one level up, but the
              legs are where the numbers come from, and a column of five
              figures with no total underneath makes you do the arithmetic the
              app has already done. Only the columns that add up get one. */}
          {rows.length > 1 && (
            
              <tr className="border-b-2 border-line-strong text-copy">
                {mover && <td aria-hidden />}
                {columns.map((c, i) => {
                  const total = summariseRows(c, rows)
                  return (
                    <td
                      key={c.id}
                      className={`pb-2.5 pt-1 pr-7 ${
                        c.align === 'right' ? 'text-right' : 'text-left'
                      } ${total === null ? 'text-faint' : `num font-medium ${toneClass(c, total)}`}`}
                    >
                      {i === 0 ? (
                        <span className="label whitespace-nowrap text-note">All {rows.length} open legs</span>
                      ) : total === null ? (
                        ''
                      ) : (
                        formatField(c, total)
                      )}
                    </td>
                  )
                })}
                <td aria-hidden />
              </tr>
            
          )}
          </thead>
          <tbody>
            {rows.map((values, i) => {
              const symbol = String(values.symbol ?? '')
              const movable = !!mover && symbol !== ''
              return (
              <tr
                key={i}
                className={`border-t border-line/70 ${movable ? 'cursor-grab active:cursor-grabbing' : ''} ${
                  mover?.dragged?.symbol === symbol && movable ? 'opacity-50' : ''
                }`}
                draggable={movable}
                onDragStart={(e) => {
                  if (!mover || !movable) return
                  e.dataTransfer.effectAllowed = 'move'
                  e.dataTransfer.setData('text/plain', symbol)
                  mover.setDragged({ symbol, fromId })
                }}
                onDragEnd={() => mover?.setDragged(null)}
              >
                {mover && (
                  <td className="whitespace-nowrap py-2.5 pr-3">
                    {movable && (
                      <span className="flex items-center gap-1.5">
                        <span aria-hidden title="Drag this leg onto another row" className="select-none text-faint">
                          ⠿
                        </span>
                        <select
                          aria-label="Move this leg to another trade"
                          value=""
                          onChange={(e) => {
                            const to = e.target.value
                            if (to === '') return
                            void mover.move(symbol, fromId, to === '__alone' ? null : to)
                          }}
                          className="w-[4.5rem] rounded-sm border border-line bg-transparent px-1 py-0.5 text-caption text-muted"
                        >
                          <option value="">Move…</option>
                          {rows.length > 1 && <option value="__alone">On its own</option>}
                          {mover.targets(fromId).map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.label}
                            </option>
                          ))}
                        </select>
                      </span>
                    )}
                  </td>
                )}
                {columns.map((c) => {
                  const raw = values[c.id] ?? null
                  const numeric =
                    c.format !== 'text' && c.format !== 'date' && c.format !== 'level'
                  if (c.id === 'leg') {
                    const short = String(values.side ?? '') === 'short'
                    return (
                      <td key={c.id} className="whitespace-nowrap py-2.5 pr-7">
                        <span
                          className={`mr-2 inline-block w-12 rounded px-1 py-0.5 text-center text-caption uppercase tracking-wide ${
                            short ? 'bg-accent-soft text-accent' : 'bg-sunken text-muted'
                          }`}
                        >
                          {short ? '-' : '+'}
                          {Number(values.quantity ?? 1)}
                        </span>
                        {String(values.strike ?? '') === ''
                          ? String(values.right ?? '')
                          : `${values.strike} ${values.right}`}
                        {(values as Record<string, unknown>).estimated === true && <Estimate />}
                      </td>
                    )
                  }
                  return (
                    <td
                      key={c.id}
                      className={`whitespace-nowrap py-2.5 pr-7 ${c.align === 'right' ? 'text-right' : 'text-left'} ${
                        numeric ? 'figure' : 'text-muted'
                      } ${toneClass(c, raw)}`}
                    >
                      {formatField(c, raw)}
                    </td>
                  )
                })}
                <td aria-hidden />
              </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      </div>
    </div>
  )
}
