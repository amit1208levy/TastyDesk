import { useState } from 'react'
import { byGroup, type FieldSpec } from '../lib/fields'

/* Choosing what a table shows, and in what order.

   Two lists side by side. On the left, everything the app can measure, grouped
   the way a trader thinks about it — identity, time, money, risk, greeks. On
   the right, what is on screen now, in the order it appears, each row able to
   move up or down. No drag and drop: a list that has to be dragged is a list
   that cannot be used with one hand while the market is open. */
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

  return (
    <div className="surface sheened mt-3 p-5">
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

      <div className="mt-4 grid gap-5 lg:grid-cols-[1fr_20rem]">
        <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
          {byGroup(catalogue).map(([group, fields]) => (
            <div key={group}>
              <div className="label mb-1.5">{group}</div>
              <ul className="space-y-0.5">
                {fields.map((f) => {
                  const on = draft.includes(f.id)
                  return (
                    <li key={f.id}>
                      <button
                        onClick={() => toggle(f.id)}
                        title={f.hint}
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
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>

        <div className="rounded-card border border-line bg-sunken/50 p-3">
          <div className="label mb-2">Order on screen</div>
          <ol className="space-y-1">
            {draft.map((id, i) => {
              const f = byId.get(id)
              if (!f) return null
              return (
                <li
                  key={id}
                  className="flex items-center gap-2 rounded-sm bg-raised px-2 py-1.5 text-[14px]"
                >
                  <span className="num w-5 shrink-0 text-[12px] text-faint">{i + 1}</span>
                  <span className="flex-1 truncate">{f.label}</span>
                  <button
                    onClick={() => move(i, -1)}
                    disabled={i === 0}
                    aria-label={`Move ${f.label} up`}
                    className="px-1 text-muted hover:text-ink disabled:opacity-25"
                  >
                    ↑
                  </button>
                  <button
                    onClick={() => move(i, 1)}
                    disabled={i === draft.length - 1}
                    aria-label={`Move ${f.label} down`}
                    className="px-1 text-muted hover:text-ink disabled:opacity-25"
                  >
                    ↓
                  </button>
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
