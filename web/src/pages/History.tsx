import { useMemo, useState } from 'react'
import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { RollCandidates } from '../components/RollCandidates'
import { NeedsReview } from '../components/NeedsReview'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, fullDate, num, signedClass, EM_DASH } from '../lib/format'
import type { NamedStrategy, StrategyView } from '../types'

/* What each year came to, and what is still riding on this one.

   The figure at the top of this page is realized: money actually taken, net of
   fees, on trades that are finished. It is the honest number for a year that is
   over and an incomplete one for the year you are in, where the open positions
   are neither counted nor visible. So the current year shows all three — what
   was banked, what is still open, and the two together — and a finished year
   shows only what was banked, because nothing is riding on it any more. */
function ByYear({ views, open }: { views: StrategyView[]; open: string | null }) {
  const thisYear = new Date().getFullYear()
  const realized = new Map<number, number>()
  for (const v of views) {
    const closed = v.strategy.closed_at
    if (!closed) continue
    const year = new Date(closed).getFullYear()
    realized.set(year, (realized.get(year) ?? 0) + (num(v.strategy.realized_pnl) ?? 0))
  }
  const years = [...realized.keys()].sort((a, b) => b - a)
  if (years.length === 0) return null
  const openPnl = num(open)

  return (
    <div className="sheened rounded-card border border-line bg-raised px-4 py-3 shadow-[var(--shadow-sm)]">
      <div className="label">By year — realized is money taken, net of fees</div>
      <ul className="mt-2 space-y-1.5">
        {years.map((year) => {
          const banked = realized.get(year) ?? 0
          const running = year === thisYear
          const all = openPnl === null ? null : banked + openPnl
          return (
            <li key={year} className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-[16px]">
              <span className="num w-12 shrink-0 font-semibold">{year}</span>
              <span>
                <span className={`figure font-medium ${signedClass(banked)}`}>
                  {money(banked, { sign: true, cents: false })}
                </span>
                <span className="text-muted"> realized</span>
              </span>
              {running && (
                <>
                  <span>
                    <span className={`figure font-medium ${signedClass(open)}`}>
                      {money(open, { sign: true, cents: false })}
                    </span>
                    <span className="text-muted"> still open</span>
                  </span>
                  <span>
                    <span className={`figure font-semibold ${signedClass(all)}`}>
                      {all === null ? EM_DASH : money(all, { sign: true, cents: false })}
                    </span>
                    <span className="text-muted"> the year so far</span>
                  </span>
                  {openPnl === null && (
                    <span className="text-[14px] text-warn">
                      one position has no price, so the two cannot be added
                    </span>
                  )}
                </>
              )}
            </li>
          )
        })}
      </ul>
      <p className="mt-2 text-[14px] text-muted">
        A finished year shows only what was banked — nothing is riding on it any more. What is
        still open is a mark that can move, not a result.
      </p>
    </div>
  )
}


/* Building a strategy out of trades you have already made.

   A strategy in this app is defined by example: you point at the trades that
   are the same idea and name it. Until now that could only be done from the
   open legs, which meant the app could only learn from what you happen to hold
   today — and the thing worth learning from is three years of history.

   It is also how the matching gets better, not merely how the list gets
   longer. Every candidate is scored against the members, so each trade added
   here widens what counts as this strategy: its leg shapes, its structures,
   its usual size. Add the 2024 version of a strangle and the app stops calling
   the 2023 one a stranger. */
function Builder({
  picked,
  views,
  named,
  onDone,
  onClear,
}: {
  picked: Set<string>
  views: StrategyView[]
  named: NamedStrategy[] | null
  onDone: (message: string) => void
  onClear: () => void
}) {
  const [name, setName] = useState('')
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const chosen = views.filter((v) => picked.has(v.strategy.id))
  const ids = chosen.map((v) => v.strategy.id)
  const products = [...new Set(chosen.map((v) => v.strategy.underlying.replace(/[A-Z]\d$/, '')))]

  async function run(job: () => Promise<unknown>, said: string) {
    setBusy(true)
    setProblem(null)
    try {
      await job()
      setName('')
      setTarget('')
      onDone(said)
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sticky top-2 z-10 rounded-card border border-accent/40 bg-raised px-4 py-3 shadow-[var(--shadow-md)]">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="num text-[17px] font-semibold">{picked.size} chosen</span>
        <span className="text-[15px] text-muted">
          {products.join(', ')}
          {products.length > 1 && (
            <span className="text-loss"> — a strategy has to be one product</span>
          )}
        </span>
        <button
          onClick={onClear}
          className="ml-auto text-[14px] text-muted transition-colors hover:text-ink"
        >
          Clear
        </button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name them as a new strategy…"
          className="min-w-[14rem] flex-1 rounded-sm border border-line bg-sunken px-3 py-1.5 text-[15px] outline-none focus:border-accent/60"
        />
        <button
          onClick={() => void run(() => api.createNamedStrategy(name.trim(), ids), `Created "${name.trim()}" from ${ids.length} trade(s).`)}
          disabled={busy || !name.trim() || ids.length === 0}
          className="rounded-sm border border-accent/50 bg-accent-soft px-3.5 py-1.5 text-[15px] text-accent disabled:opacity-40"
        >
          Create
        </button>

        <span className="px-2 text-[14px] text-faint">or</span>

        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          className="rounded-sm border border-line bg-sunken px-3 py-1.5 text-[15px] outline-none focus:border-accent/60"
        >
          <option value="">Add to a strategy you already have…</option>
          {(named ?? []).map((n) => (
            <option key={n.id} value={n.id}>
              {n.name} · {n.product} · {n.member_count} in it
            </option>
          ))}
        </select>
        <button
          onClick={() => void run(() => api.adoptMatches(target, ids), `Added ${ids.length} trade(s) to that strategy.`)}
          disabled={busy || !target || ids.length === 0}
          className="rounded-sm border border-line-strong px-3.5 py-1.5 text-[15px] text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40"
        >
          Add
        </button>
      </div>

      {problem && <div className="mt-2 text-[15px] text-loss">{problem}</div>}
      <p className="mt-2 text-[14px] text-muted">
        Every trade you put in a strategy becomes part of what the app compares against, so this
        is also how it gets better at deciding which of your older trades belong where.
      </p>
    </div>
  )
}

export function History() {
  const { data, error, loading, reload } = useAsync(() => api.closedStrategies(2000), [])
  // Only for the open figure beside this year's realized total.
  const summary = useAsync(() => api.summary(), [])
  const named = useAsync(() => api.namedStrategies(), [])
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [said, setSaid] = useState<string | null>(null)

  // trade id -> the strategies it is already in, so a row says so rather than
  // letting the same trade be added twice.
  const belongs = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const n of named.data ?? []) {
      for (const m of n.members) map.set(m.id, [...(map.get(m.id) ?? []), n.name])
    }
    return map
  }, [named.data])

  // Searched over what is printed plus the month and year, because "the /ZB
  // strangles from March" is how a trade is remembered.
  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle || !data) return data ?? []
    return data.filter((v) => {
      const s = v.strategy
      const when = s.closed_at ? new Date(s.closed_at) : null
      const hay = [
        s.underlying,
        s.strategy_type,
        s.account_number,
        ...(belongs.get(s.id) ?? []),
        when ? when.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }) : '',
        when ? String(when.getFullYear()) : '',
      ]
        .join(' ')
        .toLowerCase()
      return needle.split(/\s+/).every((word) => hay.includes(word))
    })
  }, [data, query, belongs])

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Loading closed trades" />
  if (!data || data.length === 0) {
    return <Empty title="No closed trades yet." hint="They appear here as positions are closed out." />
  }

  const totalPnl = data.reduce((acc, v) => acc + (num(v.strategy.realized_pnl) ?? 0), 0)
  const shownPnl = rows.reduce((acc, v) => acc + (num(v.strategy.realized_pnl) ?? 0), 0)
  const allShownPicked = rows.length > 0 && rows.every((v) => picked.has(v.strategy.id))

  function toggle(id: string) {
    setPicked((was) => {
      const next = new Set(was)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleShown() {
    setPicked((was) => {
      const next = new Set(was)
      for (const v of rows) {
        if (allShownPicked) next.delete(v.strategy.id)
        else next.add(v.strategy.id)
      }
      return next
    })
  }

  function done(message: string) {
    setSaid(message)
    setPicked(new Set())
    named.reload()
    reload()
  }

  return (
    <div className="space-y-4">
      <NeedsReview />

      <RollCandidates onChange={reload} />

      <ByYear views={data} open={summary.data?.open_pnl ?? null} />

      {said && (
        <div className="rounded-card border border-accent/40 bg-accent-soft px-4 py-2 text-[15px] text-accent">
          {said}
        </div>
      )}

      <SectionHeading
        title="Closed trades"
        hint={
          query.trim()
            ? `${rows.length} of ${data.length} · realized only — what you took, net of fees`
            : `${data.length} shown · realized only — what you took, net of fees`
        }
        right={
          <span className="flex items-baseline gap-2">
            <span className="text-[13px] text-muted">
              {query.trim() ? 'these trades' : 'every year shown'}
            </span>
            <span
              className={`num text-[16px] font-semibold ${signedClass(query.trim() ? shownPnl : totalPnl)}`}
            >
              {money(query.trim() ? shownPnl : totalPnl, { sign: true, cents: false })}
            </span>
          </span>
        }
      />

      <div className="flex flex-wrap items-center gap-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search — a product, a structure, a month, a strategy…"
          className="min-w-[16rem] flex-1 rounded-card border border-line bg-raised px-4 py-2 text-[16px] outline-none focus:border-accent/60"
        />
        {rows.length > 0 && (
          <button
            onClick={toggleShown}
            className="rounded-sm border border-line px-3 py-1.5 text-[14px] text-muted transition-colors hover:bg-hover hover:text-ink"
          >
            {allShownPicked ? 'Unpick these' : `Pick all ${rows.length}`}
          </button>
        )}
      </div>

      {picked.size > 0 && (
        <Builder
          picked={picked}
          views={data}
          named={named.data}
          onDone={done}
          onClear={() => setPicked(new Set())}
        />
      )}

      <div className="overflow-x-auto sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]">
        <table className="w-full min-w-[820px] text-[16px]">
          <thead>
            <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
              <th className="w-8 py-3 pl-4 pr-1 font-medium" />
              <th className="py-3 pr-3 font-medium">Closed</th>
              <th className="py-3 pr-3 font-medium">Underlying</th>
              <th className="py-3 pr-3 font-medium">Strategy</th>
              <th className="py-3 pr-3 text-right font-medium">Credit</th>
              <th className="py-3 pr-3 text-right font-medium">Realized</th>
              <th className="py-3 pr-3 text-right font-medium" title="Share of the credit kept">
                % of credit
              </th>
              <th className="py-3 pr-3 text-right font-medium">Days</th>
              <th className="py-3 pr-4 text-right font-medium">Rolls</th>
            </tr>
          </thead>
          <tbody className="num">
            {rows.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-4 text-[15px] text-faint">
                  Nothing matches “{query.trim()}”.
                </td>
              </tr>
            )}
            {rows.map((v) => {
              const s = v.strategy
              const realized = num(s.realized_pnl) ?? 0
              // Computed on the server, which knows when the question has no
              // answer: an outright futures contract has no premium to be a
              // percentage of, and dividing by whatever sits in net_credit
              // produced rows reading "+22,736% of credit".
              const share = num(v.pnl.realized_pct_of_credit)
              const days =
                s.closed_at && s.opened_at
                  ? Math.max(
                      0,
                      Math.round(
                        (new Date(s.closed_at).getTime() - new Date(s.opened_at).getTime()) / 86_400_000,
                      ),
                    )
                  : null
              const inStrategies = belongs.get(s.id) ?? []
              return (
                <tr
                  key={s.id}
                  onClick={() => toggle(s.id)}
                  className={`cursor-pointer border-b border-line/60 last:border-0 hover:bg-hover ${
                    picked.has(s.id) ? 'bg-accent-soft' : ''
                  }`}
                >
                  <td className="py-3 pl-4 pr-1">
                    <input
                      type="checkbox"
                      checked={picked.has(s.id)}
                      onChange={() => toggle(s.id)}
                      onClick={(e) => e.stopPropagation()}
                      aria-label={`Pick the ${s.underlying} ${s.strategy_type}`}
                      className="h-4 w-4 accent-[var(--accent)]"
                    />
                  </td>
                  <td className="py-3 pr-3 whitespace-nowrap text-muted">{fullDate(s.closed_at)}</td>
                  <td className="py-3 pr-3 font-medium">{s.underlying}</td>
                  <td className="py-3 pr-3 text-muted">
                    {s.strategy_type}
                    {inStrategies.length > 0 && (
                      <span className="ml-1.5 text-[13px] text-accent">{inStrategies.join(', ')}</span>
                    )}
                  </td>
                  <td className="py-3 pr-3 text-right text-muted">{money(s.net_credit, { cents: false })}</td>
                  <td className={`py-3 pr-3 text-right font-medium ${signedClass(realized)}`}>
                    {money(realized, { sign: true })}
                  </td>
                  <td
                    className={`py-3 pr-3 text-right ${
                      share !== null && Math.abs(share) > 10 ? 'text-faint' : signedClass(share)
                    }`}
                    title={
                      share !== null && Math.abs(share) > 10
                        ? `This trade's net credit was only ${money(s.net_credit)}, so the result as a share of it is not a scale worth reading.`
                        : undefined
                    }
                  >
                    {share === null
                      ? EM_DASH
                      : Math.abs(share) > 10
                        ? `${share > 0 ? '>' : '<-'}999%`
                        : pct(share, 0, true)}
                  </td>
                  <td className="py-3 pr-3 text-right text-muted">{days ?? EM_DASH}</td>
                  <td className="py-3 pr-4 text-right text-muted">{s.roll_count || EM_DASH}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
