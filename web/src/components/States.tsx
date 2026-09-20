import { Help } from './Help'
import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'
import { Onboarding } from './Onboarding'

export function Loading({ label = 'Loading' }: { label?: string }) {
  /* Three bars filling the shape the content will take, rather than a spinner
     in an empty page. The wait reads as the thing arriving, not as a stall. */
  return (
    <div className="fade-in space-y-3 py-6">
      <div className="flex items-center gap-2.5 text-[14px] text-faint">
        <span className="h-1.5 w-1.5 rounded-full bg-accent breathing" />
        <span className="uppercase tracking-[0.12em]">{label}</span>
      </div>
      <div className="loading-shimmer h-20 rounded-card" />
      <div className="loading-shimmer h-20 rounded-card opacity-70" />
      <div className="loading-shimmer h-20 rounded-card opacity-45" />
    </div>
  )
}

/* An error here is nearly always one of three things, and saying which one saves
   the user a debugging session: the backend is not running, the credentials are
   missing, or tastytrade rejected the token. */
export function ErrorPanel({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null
  const backendDown = api?.status === 0

  // Not being connected yet is a starting point, not a failure. Only real
  // failures get the red treatment.
  if (api?.status === 503) return <Onboarding detail={api.detail} />

  return (
    <div className="rounded-card border border-danger/40 bg-danger-soft px-4 py-3">
      <div className="text-[16px] font-medium text-danger">{error.message}</div>
      {api?.detail && <div className="mt-1 text-[14px] text-danger/85">{api.detail}</div>}
      {backendDown && (
        <div className="mt-2 text-[14px] text-danger/85">
          Start it with <code className="mono rounded-sm bg-danger/10 px-1">./run.sh</code> in the project
          folder.
        </div>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 rounded-sm border border-danger/40 px-2 py-1 text-[14px] text-danger transition-colors hover:bg-danger/10"
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
      <div className="text-[16px] text-muted">{title}</div>
      {hint && <div className="mt-1 text-[14px] text-faint">{hint}</div>}
    </div>
  )
}

export function SectionHeading({
  title,
  hint,
  help,
  right,
}: {
  title: string
  hint?: string
  /* An explanation rather than a status. A hint says what the page is showing
     right now — "18 shown", "written 4m ago" — and belongs on screen. An
     explanation of how the page works does not: it is read once and then read
     past forever. Pass it here and it waits on the heading instead. */
  help?: string
  right?: ReactNode
}) {
  return (
    <div className="mb-4">
      <div className="flex items-baseline gap-3">
        {help ? (
          <Help title={title} body={help}>
            <h2 className="display text-[26px] leading-tight">{title}</h2>
          </Help>
        ) : (
          <h2 className="display text-[26px] leading-tight">{title}</h2>
        )}
        {hint && <span className="text-[13px] text-muted">{hint}</span>}
        {right && <div className="ml-auto">{right}</div>}
      </div>
      <div className="rule-gold mt-2 opacity-60" />
    </div>
  )
}
