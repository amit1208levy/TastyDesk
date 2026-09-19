import type { DangerLevel } from '../types'

const STYLES: Record<DangerLevel, string> = {
  OK: 'bg-transparent text-faint border-line',
  Watch: 'bg-watch-soft text-watch border-watch/35',
  Tested: 'bg-tested-soft text-tested border-tested/35',
  Danger: 'bg-danger-soft text-danger border-danger/45',
  Critical: 'bg-critical-soft text-critical border-critical/60 font-semibold',
}

/* The one badge in the app, so it is worth setting properly: small caps, wide
   letter-spacing, a hairline border and a soft fill rather than a block of
   colour. Critical carries a slow pulse — the only thing here that moves on its
   own, and only because a position that can still be saved is worth an eye. */
export function DangerBadge({ level, className = '' }: { level: DangerLevel; className?: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase leading-none tracking-[0.14em] ${
        STYLES[level]
      } ${level === 'Critical' ? 'breathing' : ''} ${className}`}
    >
      {level}
    </span>
  )
}
