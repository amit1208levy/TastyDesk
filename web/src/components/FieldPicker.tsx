import { Help } from './Help'
import { useRef, useState } from 'react'
import { byGroup, type FieldSpec } from '../lib/fields'

/* Choosing what a table shows, and in what order.

   Two lists side by side. On the left, everything the app can measure, grouped
   the way a trader thinks about it — identity, time, money, risk, greeks. On
   the right, what is on screen now, in the order it appears, dragged into
   place. The arrow keys move a focused row the same way, so the panel still
   works without a mouse. */
export function FieldPicker({
  title,
  catalogue,
  chosen,
  onChange,
  onClose,
}: {
  title: string
  catalogue: FieldSpec[]
  chosen: string[]
  onChange: (ids: string[]) => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState<string[]>(chosen)
  const byId = new Map(catalogue.map((f) => [f.id, f]))

  function toggle(id: string) {
    setDraft((old) => (old.includes(id) ? old.filter((x) => x !== id) : [...old, id]))
  }

  function move(index: number, by: number) {
    setDraft((old) => {
      const next = [...old]
      const to = index + by
      if (to < 0 || to >= next.length) return old
      ;[next[index], next[to]] = [next[to], next[index]]
      return next
    })
  }

  /* Dragging, which is what everyone reaches for.

     This list used to move a row with a pair of arrows, on the argument that a
     list you have to drag cannot be used one-handed while the market is open.
     That argument is worth something for two items and nothing for fourteen:
     moving a column from the end to the front was eleven clicks, and the
     column of arrows beside every row was the loudest thing in the panel.

     So it drags, and the keyboard still works — arrow keys on a focused row do
     what the buttons did, which is also what a screen reader is left with when
     a drag has no meaning. */
  // Which row is moving is kept in a ref as well as in state. State is for the
  // dimming; the drop needs the value as it is now, and a drop that lands in
  // the same tick as the drag started — a fast flick, or a test — would read a
  // render behind and do nothing.
  const held = useRef<number | null>(null)
  const [dragging, setDragging] = useState<number | null>(null)
  const [over, setOver] = useState<number | null>(null)

  function drop(to: number) {
    const from = held.current
    setDraft((old) => {
      if (from === null || from === to) return old
      const next = [...old]
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next
    })
    held.current = null
    setDragging(null)
    setOver(null)
  }

  return (
    // Sized against the box it is in, not the window. It is opened from the
    // page header at full width and from inside a position drawer at a third
    // of it, and viewport breakpoints put two columns of field names into a
    // 380px panel, where every label was clipped to its checkbox.
    <div className="@container surface sheened mt-3 p-5">
      <div className="flex flex-wrap items-baseline gap-3">
        <h3 className="display text-[19px]">{title}</h3>
        <span className="text-[13px] text-muted">
          {draft.length} shown of {catalogue.length}
        </span>
        <div className="ml-auto flex gap-2">
          <button
            onClick={() => setDraft(catalogue.filter((f) => f.default).map((f) => f.id))}
            className="rounded-sm border border-line px-3 py-1 text-[13px] text-muted hover:bg-hover hover:text-ink"
          >
            Reset
          </button>
          <button
            onClick={onClose}
            className="rounded-sm border border-line px-3 py-1 text-[13px] text-muted hover:bg-hover hover:text-ink"
          >
            Cancel
          </button>
          <button
            onClick={() => onChange(draft)}
            disabled={draft.length === 0}
            className="rounded-sm border border-accent/50 bg-accent-soft px-3 py-1 text-[13px] text-accent disabled:opacity-40"
          >
            Save
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-5 @4xl:grid-cols-[1fr_20rem]">
        <div className="grid gap-x-6 gap-y-4 @lg:grid-cols-2 @3xl:grid-cols-3">
          {byGroup(catalogue).map(([group, fields]) => (
            <div key={group}>
              <div className="label mb-1.5">{group}</div>
              <ul className="space-y-0.5">
                {fields.map((f) => {
                  const on = draft.includes(f.id)
                  return (
                    <li key={f.id}>
                      {/* The place a column is chosen is the place its
                          explanation is worth most: hold on one for two
                          seconds before deciding to add it. */}
                      <Help title={f.label} body={f.help} footer={f.hint} block className="w-full">
                      <button
                        onClick={() => toggle(f.id)}
                        className={`flex w-full items-baseline gap-2 rounded-sm px-2 py-1 text-left text-[14px] ${
                          on ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-hover hover:text-ink'
                        }`}
                      >
                        <span
                          aria-hidden
                          className={`mt-0.5 inline-block h-3 w-3 shrink-0 rounded-[3px] border ${
                            on ? 'border-accent bg-accent/80' : 'border-line-strong'
                          }`}
                        />
                        <span className="truncate">{f.label}</span>
                      </button>
                      </Help>
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>

        <div className="rounded-card border border-line bg-sunken p-3">
          <div className="label mb-2">Order on screen — drag to move</div>
          <ol className="space-y-1">
            {draft.map((id, i) => {
              const f = byId.get(id)
              if (!f) return null
              return (
                <li
                  key={id}
                  draggable
                  tabIndex={0}
                  onDragStart={() => {
                    held.current = i
                    setDragging(i)
                  }}
                  onDragEnd={() => {
                    held.current = null
                    setDragging(null)
                    setOver(null)
                  }}
                  onDragOver={(e) => {
                    e.preventDefault()
                    if (over !== i) setOver(i)
                  }}
                  onDrop={(e) => {
                    e.preventDefault()
                    drop(i)
                  }}
                  onKeyDown={(e) => {
                    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return
                    e.preventDefault()
                    move(i, e.key === 'ArrowUp' ? -1 : 1)
                    // Follow the row so a run of presses keeps moving the same
                    // one rather than whatever has slid into its place.
                    const next = e.currentTarget.parentElement?.children[
                      i + (e.key === 'ArrowUp' ? -1 : 1)
                    ]
                    ;(next as HTMLElement | undefined)?.focus()
                  }}
                  aria-label={`${f.label}, position ${i + 1} of ${draft.length}. Drag to move, or use the arrow keys.`}
                  className={`flex cursor-grab items-center gap-2 rounded-sm bg-raised px-2 py-1.5 text-[14px] outline-none transition-colors active:cursor-grabbing focus:ring-1 focus:ring-accent/60 ${
                    dragging === i
                      ? 'opacity-40'
                      : over === i && dragging !== null
                        ? 'ring-1 ring-accent'
                        : ''
                  }`}
                >
                  <span aria-hidden className="shrink-0 select-none text-[13px] leading-none text-faint">
                    ⠿
                  </span>
                  <span className="num w-5 shrink-0 text-[12px] text-faint">{i + 1}</span>
                  <span className="flex-1 truncate">{f.label}</span>
                  <button
                    onClick={() => toggle(id)}
                    aria-label={`Remove ${f.label}`}
                    className="px-1 text-faint hover:text-loss"
                  >
                    ×
                  </button>
                </li>
              )
            })}
          </ol>
        </div>
      </div>
    </div>
  )
}
