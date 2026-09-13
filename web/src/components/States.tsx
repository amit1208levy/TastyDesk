import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-8 text-sm text-faint">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-accent" />
      {label}…
    </div>
  )
}

/* An error here is nearly always one of three things, and saying which one saves
   the user a debugging session: the backend is not running, the credentials are
   missing, or tastytrade rejected the token. */
export function ErrorPanel({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null
  const backendDown = api?.status === 0

  return (
    <div className="rounded-card border border-danger/40 bg-danger-soft px-4 py-3">
      <div className="text-sm font-medium text-danger">{error.message}</div>
      {api?.detail && <div className="mt-1 text-xs text-danger/85">{api.detail}</div>}
      {backendDown && (
        <div className="mt-2 text-xs text-danger/85">
          Start it with <code className="mono rounded-sm bg-danger/10 px-1">./run.sh</code> in the project
          folder.
        </div>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 rounded-sm border border-danger/40 px-2 py-1 text-xs text-danger transition-colors hover:bg-danger/10"
        >
          Try again
        </button>
      )}
    </div>
  )
}

export function Empty({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="rounded-card border border-dashed border-line bg-raised px-6 py-12 text-center">
      <div className="text-sm text-muted">{title}</div>
      {hint && <div className="mt-1 text-xs text-faint">{hint}</div>}
    </div>
  )
}

export function SectionHeading({ title, hint, right }: { title: string; hint?: string; right?: ReactNode }) {
  return (
    <div className="mb-2.5 flex items-baseline gap-3">
      <h2 className="text-[13px] font-semibold tracking-tight">{title}</h2>
      {hint && <span className="text-[11px] text-faint">{hint}</span>}
      {right && <div className="ml-auto">{right}</div>}
    </div>
  )
}
