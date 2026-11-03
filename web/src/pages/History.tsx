import { Fragment, useMemo, useState } from 'react'
import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { RollCandidates } from '../components/RollCandidates'
import { NeedsReview } from '../components/NeedsReview'
import { RollChain, RolledTag } from '../components/RollChain'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { useEra } from '../lib/era'
import { money, pct, fullDate, shortDate, num, signedClass, EM_DASH } from '../lib/format'
import type { Leg, NamedStrategy, StrategyView } from '../types'

/* The product a trade is on: /CLM6 and /CLZ6 are both /CL. */
const rootOf = (underlying: string) => underlying.replace(/[FGHJKMNQUVXZ]\d{1,2}$/, '')

/* A trade's legs, short: "-1 79C · +1 85C · 14 May 26". Enough to tell two
   strangles on the same product apart. */
function legsText(legs: Leg[]): string {
  const parts = legs.slice(0, 4).map((l) => {
    const sign = l.direction === 'Short' ? '-' : '+'
    const qty = String(Number(l.quantity))
    return l.strike && l.option_type ? `${sign}${qty} ${Number(l.strike)}${l.option_type}` : `${sign}${qty} ${l.symbol}`
  })
  if (legs.length > 4) parts.push(`+${legs.length - 4} more`)
  const expiries = [...new Set(legs.map((l) => l.expiration).filter((e): e is string => Boolean(e)))]
    .sort()
    .map((e) => new Date(`${e}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' }))
  return [...parts, ...expiries].join(' · ')
}

/* The latest a strategy was traded: what "newest first" sorts on. */
const lastTraded = (n: NamedStrategy) => n.members.reduce((m, x) => (x.opened > m ? x.opened : m), '')

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
            <li key={year} className="flex flex-wrap items-baseline gap-x-5 gap-y-1 text-copy">
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
                    <span className="text-body text-warn">
                      one position has no price, so the two cannot be added
                    </span>
                  )}
                </>
              )}
            </li>
          )
        })}
      </ul>
      <p className="mt-2 text-body text-muted">
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
  const products = [...new Set(chosen.map((v) => rootOf(v.strategy.underlying)))]
  // Only the strategies on the product picked, the one traded most recently first.
  const [everyProduct, setEveryProduct] = useState(false)
  const choices = (named ?? [])
    .filter((n) => everyProduct || products.includes(rootOf(n.product)))
    .sort((a, b) => lastTraded(b).localeCompare(lastTraded(a)))

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
        <span className="num text-lead font-semibold">{picked.size} chosen</span>
        <span className="text-copy text-muted">
          {products.join(', ')}
          {products.length > 1 && (
            <span className="text-loss"> — a strategy has to be one product</span>
          )}
        </span>
        <button
          onClick={onClear}
          className="ml-auto text-body text-muted transition-colors hover:text-ink"
        >
          Clear
        </button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name them as a new strategy…"
          className="min-w-[14rem] flex-1 rounded-sm border border-line bg-sunken px-3 py-1.5 text-copy outline-none focus:border-accent/60"
        />
        <button
          onClick={() => void run(() => api.createNamedStrategy(name.trim(), ids), `Created "${name.trim()}" from ${ids.length} trade(s).`)}
          disabled={busy || !name.trim() || ids.length === 0}
          className="rounded-sm border border-accent/50 bg-accent-soft px-3.5 py-1.5 text-copy text-accent disabled:opacity-40"
        >
          Create
        </button>

        <span className="px-2 text-body text-faint">or</span>

        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          className="rounded-sm border border-line bg-sunken px-3 py-1.5 text-copy outline-none focus:border-accent/60"
        >
          <option value="">
            {choices.length ? `Add to one of your ${products.join(', ')} strategies…` : `No ${products.join(', ')} strategy yet`}
          </option>
          {choices.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name} · {n.product} · {n.member_count} in it
              {lastTraded(n) ? ` · last ${shortDate(lastTraded(n))}` : ''}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-note text-muted">
          <input
            type="checkbox"
            checked={everyProduct}
            onChange={(e) => setEveryProduct(e.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--accent)]"
          />
          every product
        </label>
        <button
          onClick={() => void run(() => api.adoptMatches(target, ids), `Added ${ids.length} trade(s) to that strategy.`)}
          disabled={busy || !target || ids.length === 0}
          className="rounded-sm border border-line-strong px-3.5 py-1.5 text-copy text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40"
        >
          Add
        </button>
      </div>

      {problem && <div className="mt-2 text-copy text-loss">{problem}</div>}
      <p className="mt-2 text-body text-muted">
        Every trade you put in a strategy becomes part of what the app compares against, so this
        is also how it gets better at deciding which of your older trades belong where.
      </p>
    </div>
  )
}

export function History() {
  const { data: everything, error, loading, reload } = useAsync(() => api.closedStrategies(2000), [])
  // New Levy: only what closed since the fresh start, unless the old self is shown.
  const era = useEra()
  const named = useAsync(() => api.namedStrategies(), [])
  // Counted as the reports count it: closed since the start, and either new
  // or part of a strategy carried in. Closing old positions is the old life.
  const data = useMemo(() => {
    const since = era.since
    if (since === null) return everything
    const carried = new Set(
      (named.data ?? []).filter((n) => era.isKept(n.id, n.members)).flatMap((n) => n.members.map((m) => m.id)),
    )
    const whole = new Set(
      (named.data ?? []).filter((n) => era.countsWhole(n.id)).flatMap((n) => n.members.map((m) => m.id)),
    )
    return everything?.filter(
      (v) =>
        whole.has(v.strategy.id) ||
        ((v.strategy.closed_at ?? '').slice(0, 10) >= since &&
          (v.strategy.opened_at.slice(0, 10) >= since || carried.has(v.strategy.id))),
    )
  }, [everything, named.data, era])
  // Only for the open figure beside this year's realized total.
  const summary = useAsync(() => api.summary(), [])
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  // The closed trade whose rolls are showing, opened from its "rolled N×".
  const [rollsFor, setRollsFor] = useState<string | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [onlyLoose, setOnlyLoose] = useState(false)
  const [product, setProduct] = useState('')
  const [adding, setAdding] = useState<string | null>(null)
  // What each named strategy thinks belongs to it, turned round: for each
  // trade, the strategy it most likely belongs to.
  const matches = useAsync(() => api.allMatches(), [])
  const suggestion = useMemo(() => {
    const best = new Map<string, { id: string; name: string; confidence: number }>()
    const names = new Map((named.data ?? []).map((n) => [n.id, n.name]))
    for (const [id, report] of Object.entries(matches.data ?? {})) {
      for (const c of report.candidates) {
        if (c.confidence < 0.6) continue
        const was = best.get(c.trade_id)
        if (!was || c.confidence > was.confidence) {
          best.set(c.trade_id, { id, name: names.get(id) ?? id, confidence: c.confidence })
        }
      }
    }
    return best
  }, [matches.data, named.data])

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
    const base = (data ?? []).filter(
      (v) =>
        (!onlyLoose || !belongs.has(v.strategy.id)) && (!product || rootOf(v.strategy.underlying) === product),
    )
    if (!needle) return base
    return base.filter((v) => {
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
  }, [data, query, belongs, onlyLoose, product])
  const productList = useMemo(
    () => [...new Set((data ?? []).map((v) => rootOf(v.strategy.underlying)))].sort(),
    [data],
  )
  const looseCount = (data ?? []).filter((v) => !belongs.has(v.strategy.id)).length

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
    matches.reload()
    reload()
  }

  async function addOne(tradeId: string, namedId: string, name: string) {
    setAdding(tradeId)
    try {
      await api.adoptMatches(namedId, [tradeId])
      done(`Added to ${name}.`)
    } catch (e) {
      setSaid(e instanceof Error ? e.message : String(e))
    } finally {
      setAdding(null)
    }
  }

  return (
    <div className="space-y-4">
      <NeedsReview />

      <RollCandidates onChange={reload} />

      <ByYear views={data} open={summary.data?.open_pnl ?? null} />

      {said && (
        <div className="rounded-card border border-accent/40 bg-accent-soft px-4 py-2 text-copy text-accent">
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
            <span className="text-note text-muted">
              {query.trim() ? 'these trades' : 'every year shown'}
            </span>
            <span
              className={`num text-copy font-semibold ${signedClass(query.trim() ? shownPnl : totalPnl)}`}
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
          className="min-w-[16rem] flex-1 rounded-card border border-line bg-raised px-4 py-2 text-copy outline-none focus:border-accent/60"
        />
        <div className="flex rounded-sm border border-line text-body">
          <button
            onClick={() => setOnlyLoose(false)}
            className={`px-3 py-1.5 ${!onlyLoose ? 'bg-accent-soft text-accent' : 'text-muted hover:text-ink'}`}
          >
            All
          </button>
          <button
            onClick={() => setOnlyLoose(true)}
            className={`border-l border-line px-3 py-1.5 ${onlyLoose ? 'bg-accent-soft text-accent' : 'text-muted hover:text-ink'}`}
          >
            Not in a strategy · {looseCount}
          </button>
        </div>
        <select
          value={product}
          onChange={(e) => setProduct(e.target.value)}
          className="rounded-sm border border-line bg-raised px-2.5 py-1.5 text-body"
          aria-label="Product"
        >
          <option value="">Every product</option>
          {productList.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        {rows.length > 0 && (
          <button
            onClick={toggleShown}
            className="rounded-sm border border-line px-3 py-1.5 text-body text-muted transition-colors hover:bg-hover hover:text-ink"
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
        <table className="w-full text-copy">
          <thead>
            <tr className="border-b border-line text-left text-caption uppercase tracking-wider text-faint">
              <th className="w-8 py-3 pl-4 pr-1 font-medium" />
              <th className="py-3 pr-3 font-medium">Closed</th>
              <th className="py-3 pr-3 font-medium">Underlying</th>
              <th className="w-full py-3 pr-3 font-medium">Strategy</th>
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
                <td colSpan={9} className="px-4 py-4 text-copy text-faint">
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
                <Fragment key={s.id}>
                <tr
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
                  <td className="py-3 pr-3 whitespace-nowrap text-muted">
                    {fullDate(s.closed_at)}
                    <div className="text-note text-faint">opened {shortDate(s.opened_at)}</div>
                  </td>
                  <td className="py-3 pr-3 font-medium">{s.underlying}</td>
                  <td className="py-3 pr-3 text-muted">
                    {s.strategy_type}
                    {inStrategies.length > 0 && (
                      <span className="ml-1.5 text-note text-accent">{inStrategies.join(', ')}</span>
                    )}
                    {s.legs.length > 0 && <div className="text-note text-faint">{legsText(s.legs)}</div>}
                    {inStrategies.length === 0 && suggestion.has(s.id) && (() => {
                      const g = suggestion.get(s.id)!
                      return (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            void addOne(s.id, g.id, g.name)
                          }}
                          disabled={adding === s.id}
                          title={`Looks like ${g.name}: ${Math.round(g.confidence * 100)}% sure. Click to add it.`}
                          className="mt-1 rounded-full border border-accent/50 bg-accent-soft px-2.5 py-0.5 font-sans text-note font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
                        >
                          {adding === s.id ? 'Adding…' : `+ Add to ${g.name}`}
                        </button>
                      )
                    })()}
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
                  <td className="py-3 pr-4 text-right text-muted">
                    {s.roll_count > 0 ? (
                      <RolledTag
                        count={s.roll_count}
                        shown={rollsFor === s.id}
                        onClick={() => setRollsFor(rollsFor === s.id ? null : s.id)}
                      />
                    ) : (
                      EM_DASH
                    )}
                  </td>
                </tr>
                {rollsFor === s.id && (
                  <tr className="bg-sunken">
                    <td colSpan={9} className="px-4 py-3">
                      <RollChain
                        strategyId={s.id}
                        rolls={s.rolls ?? []}
                        rollCount={s.roll_count}
                        holding={null}
                        onClose={() => setRollsFor(null)}
                      />
                    </td>
                  </tr>
                )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
