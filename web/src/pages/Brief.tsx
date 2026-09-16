import { Markdown } from '../components/Markdown'
import { Loading, ErrorPanel, SectionHeading } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { fullDate, relativeTime } from '../lib/format'

export function Brief() {
  const { data, error, loading, reload } = useAsync(() => api.brief(), [])

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Looking for this morning's brief" />

  if (!data?.available || !data.brief) {
    return (
      <div className="mx-auto max-w-2xl rounded-card border border-line bg-raised p-6">
        <h2 className="text-base font-semibold">No brief yet</h2>
        <p className="mt-1 text-sm text-muted">
          The brief is written by a scheduled Claude session rather than by the app calling a model,
          so no Anthropic key has to sit on this machine next to your brokerage credential — and it
          costs nothing per day.
        </p>
        <p className="mt-3 text-sm text-muted">
          Ask me to set up the schedule, or to write one now, and it appears here.
        </p>
      </div>
    )
  }

  const b = data.brief

  return (
    <div className="mx-auto max-w-3xl space-y-3">
      <SectionHeading
        title="Morning brief"
        hint={`${fullDate(b.on)} · written ${relativeTime(b.written_at)}`}
      />

      {b.is_stale && (
        <div className="rounded-card border border-watch/40 bg-watch-soft px-3 py-2 text-xs text-watch">
          This is the brief from {fullDate(b.on)}, not today's. The numbers in it are that old —
          the Positions tab is live.
        </div>
      )}

      <div className="rounded-card border border-line bg-raised px-5 py-4">
        <Markdown source={b.markdown} />
      </div>
    </div>
  )
}
