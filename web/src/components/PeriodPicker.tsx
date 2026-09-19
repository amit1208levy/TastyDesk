import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import type { Period } from '../types'

export const ALL_TIME: Period = { from: null, to: null, label: 'All time' }

const MONTH_NAMES = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

function monthLabel(key: string): string {
  const [y, m] = key.split('-')
  return `${MONTH_NAMES[Number(m) - 1]} ${y.slice(2)}`
}

function lastDayOf(year: number, month: number): string {
  const d = new Date(Date.UTC(year, month, 0))
  return d.toISOString().slice(0, 10)
}

/* Which period a report covers.

   Only periods that contain closed trades are offered. A month with nothing in
   it invites the reader to conclude they had a flat month, when in fact they
   had no month at all — and the index the server returns is the only thing that
   knows the difference. */
export function PeriodPicker({
  value,
  onChange,
}: {
  value: Period
  onChange: (p: Period) => void
}) {
  const index = useAsync(() => api.periods(), [])
  const [custom, setCustom] = useState(false)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const years = index.data?.years ?? []
  const months = (index.data?.months ?? []).slice(0, 18)

  function pick(p: Period) {
    setCustom(false)
    onChange(p)
  }

  const isOn = (p: Period) => value.from === p.from && value.to === p.to

  return (
    <div className="sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)] px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[12px] uppercase tracking-wider text-faint">Period</span>

        <button
          onClick={() => pick(ALL_TIME)}
          className={`rounded-sm border px-2 py-0.5 text-[13px] ${
            isOn(ALL_TIME) && !custom
              ? 'border-accent/50 bg-accent-soft text-accent'
              : 'border-line text-muted hover:bg-hover'
          }`}
        >
          All time
        </button>

        {years.map((y) => {
          const p: Period = {
            from: `${y.year}-01-01`,
            to: `${y.year}-12-31`,
            label: String(y.year),
          }
          return (
            <button
              key={y.year}
              onClick={() => pick(p)}
              title={`${y.trades} closed trades`}
              className={`rounded-sm border px-2 py-0.5 text-[13px] ${
                isOn(p) && !custom
                  ? 'border-accent/50 bg-accent-soft text-accent'
                  : 'border-line text-muted hover:bg-hover'
              }`}
            >
              {y.year}
              <span className="ml-1 text-faint">{y.trades}</span>
            </button>
          )
        })}

        <select
          value={custom ? '' : (value.label.includes(' ') ? value.label : '')}
          onChange={(e) => {
            const key = e.target.value
            if (!key) return
            const [y, m] = key.split('-').map(Number)
            pick({
              from: `${key}-01`,
              to: lastDayOf(y, m),
              label: monthLabel(key),
            })
          }}
          className="rounded-sm border border-line bg-bg px-1.5 py-0.5 text-[13px] text-muted outline-none focus:border-accent"
        >
          <option value="">Month…</option>
          {months.map((m) => (
            <option key={m.month} value={m.month}>
              {monthLabel(m.month)} · {m.trades}
            </option>
          ))}
        </select>

        <button
          onClick={() => setCustom(!custom)}
          className={`rounded-sm border px-2 py-0.5 text-[13px] ${
            custom ? 'border-accent/50 bg-accent-soft text-accent' : 'border-line text-muted hover:bg-hover'
          }`}
        >
          Custom
        </button>

        <span className="ml-auto text-[13px] text-faint">
          {value.from || value.to
            ? `${value.from ?? 'the start'} → ${value.to ?? 'today'}`
            : index.data?.first_close
              ? `${index.data.first_close} → ${index.data.last_close}`
              : ''}
        </span>
      </div>

      {custom && (
        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-line pt-2">
          <label className="text-[13px] text-muted">
            From{' '}
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="rounded-sm border border-line bg-bg px-1.5 py-0.5 text-[13px] outline-none focus:border-accent"
            />
          </label>
          <label className="text-[13px] text-muted">
            To{' '}
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="rounded-sm border border-line bg-bg px-1.5 py-0.5 text-[13px] outline-none focus:border-accent"
            />
          </label>
          <button
            onClick={() =>
              onChange({
                from: from || null,
                to: to || null,
                label: `${from || 'start'} → ${to || 'today'}`,
              })
            }
            disabled={!from && !to}
            className="rounded-sm border border-accent/50 bg-accent-soft px-2 py-0.5 text-[13px] text-accent disabled:opacity-40"
          >
            Apply
          </button>
          <span className="text-[12px] text-faint">
            A trade counts in the period it closed in — that is when the money was made.
          </span>
        </div>
      )}
    </div>
  )
}
