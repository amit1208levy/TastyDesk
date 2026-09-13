import type { ReactNode } from 'react'

/* A headline figure. The label sits above the value in small caps, the value
   carries the weight, and a single line of context sits beneath. Nothing is
   coloured unless the number itself has a direction. */
export function StatTile({
  label,
  value,
  sub,
  tone = 'neutral',
  title,
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'neutral' | 'profit' | 'loss' | 'muted'
  title?: string
}) {
  const toneClass =
    tone === 'profit' ? 'text-profit' : tone === 'loss' ? 'text-loss' : tone === 'muted' ? 'text-muted' : 'text-ink'
  return (
    <div
      className="rounded-card border border-line bg-raised px-4 py-3 min-w-0"
      title={title}
    >
      <div className="text-[11px] font-medium uppercase tracking-wider text-faint truncate">{label}</div>
      <div className={`num mt-1 text-[22px] font-semibold leading-tight tabular-nums truncate ${toneClass}`}>
        {value}
      </div>
      {sub !== undefined && <div className="mt-0.5 truncate text-xs text-muted">{sub}</div>}
    </div>
  )
}
