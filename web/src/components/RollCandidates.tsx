import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import type { RollCandidate } from '../types'

const TONE: Record<RollCandidate['confidence'], string> = {
  high: 'border-accent/40 bg-accent-soft text-accent',
  likely: 'border-line bg-sunken text-muted',
  possible: 'border-line bg-sunken text-faint',
}

/* Rolls done as two orders cannot be detected from the fills alone: closing one
   trade and opening another the same afternoon is ordinary behaviour, not
   necessarily a roll. So these are proposed rather than applied.

   It matters because an unlinked roll reads as a loser followed by an unrelated
   winner, which flatters the win rate and hides what rolling actually costs. */
export function RollCandidates({ onChange }: { onChange?: () => void }) {
  const { data, reload } = useAsync(() => api.rollCandidates(), [])
  const [busy, setBusy] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState(false)

  const key = (c: RollCandidate) => `${c.closed_id}|${c.opened_id}`
  const pending = (data ?? []).filter((c) => !dismissed.has(key(c)))

  if (pending.length === 0) return null

  async function link(c: RollCandidate) {
    setBusy(key(c))
    try {
      await api.linkStrategies([c.closed_id, c.opened_id])
      setDismissed((d) => new Set(d).add(key(c)))
      reload()
      onChange?.()
    } finally {
      setBusy(null)
    }
  }

  const shown = expanded ? pending : pending.slice(0, 3)

  return (
    <div className="rounded-card border border-line bg-raised p-4">
      <div className="flex items-baseline gap-2">
        <h3 className="text-[15px] font-semibold">
          {pending.length} possible roll{pending.length === 1 ? '' : 's'}
        </h3>
        <span className="text-[13px] text-faint">
          executed as two orders, so they were not linked automatically
        </span>
      </div>
      <p className="mt-1 text-[14px] text-muted">
        Left apart, a roll reads as a loser followed by an unrelated winner — which flatters your win
        rate and hides what rolling costs you.
      </p>

      <ul className="mt-3 space-y-2">
        {shown.map((c) => (
          <li key={key(c)} className="flex flex-wrap items-start gap-2 border-t border-line/70 pt-2">
            <span
              className={`shrink-0 rounded-full border px-2 py-0.5 text-[12px] uppercase tracking-wide ${TONE[c.confidence]}`}
            >
              {c.confidence}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[14px] text-ink">{c.reason}</div>
              <div className="mono mt-0.5 text-[12px] text-faint">
                {c.closed_id} + {c.opened_id}
              </div>
            </div>
            <div className="flex shrink-0 gap-1">
              <button
                onClick={() => link(c)}
                disabled={busy === key(c)}
                className="rounded-sm border border-line-strong px-2 py-0.5 text-[13px] transition-colors hover:bg-hover disabled:opacity-50"
              >
                {busy === key(c) ? 'Linking…' : 'One trade'}
              </button>
              <button
                onClick={() => setDismissed((d) => new Set(d).add(key(c)))}
                className="rounded-sm border border-line px-2 py-0.5 text-[13px] text-muted transition-colors hover:bg-hover"
              >
                Separate
              </button>
            </div>
          </li>
        ))}
      </ul>

      {pending.length > 3 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-2 text-[13px] text-muted transition-colors hover:text-ink"
        >
          {expanded ? 'Show fewer' : `Show ${pending.length - 3} more`}
        </button>
      )}
    </div>
  )
}
