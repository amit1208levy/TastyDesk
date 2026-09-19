import { useState } from 'react'
import { ErrorPanel, Loading, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { relativeTime, fullDate } from '../lib/format'
import type { AppEvent } from '../types'

const SEVERITY: Record<AppEvent['severity'], { dot: string; text: string }> = {
  info: { dot: 'bg-line-strong', text: 'text-muted' },
  notable: { dot: 'bg-accent', text: 'text-ink' },
  warning: { dot: 'bg-tested', text: 'text-ink' },
  error: { dot: 'bg-danger', text: 'text-danger' },
}

const FILTERS = [
  { id: undefined, label: 'Everything' },
  { id: 'notable', label: 'Worth knowing' },
  { id: 'warning', label: 'Needs hands' },
] as const

/* What actually happened, in order.

   Each crossing is logged once, so this is a list of things that CHANGED
   rather than a list of things that are currently true — which is what makes
   it worth reading rather than another view of the same table. */
export function Activity() {
  const [severity, setSeverity] = useState<string | undefined>(undefined)
  const { data, error, loading, reload } = useAsync(
    () => api.events(200, severity),
    [severity],
    30_000,
  )

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Reading the log" />

  const events = data ?? []
  const byDay = new Map<string, AppEvent[]>()
  for (const e of events) {
    const day = e.at.slice(0, 10)
    const list = byDay.get(day)
    if (list) list.push(e)
    else byDay.set(day, [e])
  }

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <SectionHeading
        title="Activity"
        hint="things that changed — each one logged once, not repeated while it stays true"
        right={
          <div className="flex gap-1">
            {FILTERS.map((f) => (
              <button
                key={f.label}
                onClick={() => setSeverity(f.id)}
                className={`rounded-sm px-2 py-0.5 text-[13px] transition-colors ${
                  severity === f.id ? 'bg-sunken font-medium text-ink' : 'text-muted hover:bg-hover'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        }
      />

      {events.length === 0 ? (
        <Empty
          title="Nothing logged yet."
          hint="Syncs, positions opening and closing, and the moment a position crosses one of your rules all land here."
        />
      ) : (
        <div className="space-y-4">
          {[...byDay.entries()].map(([day, list]) => (
            <div key={day}>
              <div className="mb-1.5 text-[13px] font-medium uppercase tracking-wider text-faint">
                {fullDate(day)}
              </div>
              <div className="overflow-hidden rounded-card border border-line bg-raised">
                {list.map((e) => {
                  const tone = SEVERITY[e.severity] ?? SEVERITY.info
                  return (
                    <div
                      key={e.id}
                      className="flex items-baseline gap-2.5 border-b border-line/60 px-3.5 py-3 last:border-0"
                    >
                      <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${tone.dot}`} />
                      <div className="min-w-0 flex-1">
                        <div className={`text-[16px] ${tone.text}`}>{e.summary}</div>
                        <div className="mono text-[12px] text-faint">{e.kind}</div>
                      </div>
                      <span className="shrink-0 text-[12px] text-faint">{relativeTime(e.at)}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
