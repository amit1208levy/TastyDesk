import type { DangerLevel } from '../types'

const STYLES: Record<DangerLevel, string> = {
  OK:       'bg-sunken text-muted border-line',
  Watch:    'bg-watch-soft text-watch border-watch/30',
  Tested:   'bg-tested-soft text-tested border-tested/30',
  Danger:   'bg-danger-soft text-danger border-danger/40',
  Critical: 'bg-critical-soft text-critical border-critical/50 font-semibold',
}

export function DangerBadge({ level, className = '' }: { level: DangerLevel; className?: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[13px] leading-tight tracking-wide uppercase ${STYLES[level]} ${className}`}
    >
      {level}
    </span>
  )
}
