import { Fragment, useCallback, useEffect, useState } from 'react'
import { useWidth } from '../lib/useMeasure'
import { Help } from './Help'
import { DangerBadge } from './DangerBadge'
import { RiskScale } from './RiskScale'
import { LegDetail } from './LegDetail'
import { PayoffPanel } from './PayoffPanel'
import { money, pct, decimals, num, EM_DASH } from '../lib/format'
import { formatField, toneClass, type FieldSpec } from '../lib/fields'
import type { StrategyView, DangerLevel } from '../types'
import { DANGER_ORDER } from '../types'

/* The table's own scroller, found in the document rather than held in a ref.

   A ref here was null by the time a click arrived, often enough to make the
   button look broken, and chasing why across React's re-render timing was not
   worth it: there is exactly one positions table on the page, it is marked,
   and looking it up cannot go stale. */
function findScroller(): HTMLDivElement | null {
  return document.querySelector<HTMLDivElement>('[data-positions-scroller]')
}

function rank(level: DangerLevel): number {
  return DANGER_ORDER.indexOf(level)
}

const VERDICT_TONE: Record<string, string> = {
  act: 'text-loss',
  take: 'text-profit',
  watch: 'text-tested',
  none: 'text-faint',
}

/* The severity of a row, drawn rather than coloured.

   A badge that differs only in hue is invisible in peripheral vision, which is
   how a table is actually read. This is a bar down the left edge of the row:
   taller and brighter as the position gets worse, so the shape of the list is
   legible before any word is. */
function EdgeBar({ level }: { level: DangerLevel }) {
  const height = { OK: 'h-2', Watch: 'h-4', Tested: 'h-7', Danger: 'h-10', Critical: 'h-full' }[
    level
  ]
  const colour = {
    OK: 'bg-line-strong',
    Watch: 'bg-watch',
    Tested: 'bg-tested',
    Danger: 'bg-danger',
    Critical: 'bg-critical',
  }[level]
  return (
    <span
      aria-hidden
      className={`absolute left-0 top-1/2 w-[3px] -translate-y-1/2 rounded-r ${height} ${colour}`}
    />
  )
}

/* One cell. Almost every field is printed straight from the catalogue; the
   three that are drawn rather than printed — the ticker with its roll count,
   the risk badge, the bar showing where the position sits on its own risk —
   are named here and nowhere else. */
function Cell({ spec, view, first }: { spec: FieldSpec; view: StrategyView; first: boolean }) {
  const pad = first ? 'py-3.5 pl-4 pr-3' : 'py-3.5 pr-3'
  const align = spec.align === 'right' ? 'text-right' : 'text-left'
  const raw = (view.values ?? {})[spec.id] ?? null

  if (spec.id === 'verdict') {
    const v = view.verdict
    if (!v) return <td className={pad} />
    return (
      // Capped, because the reason is a sentence and a sentence will take the
      // whole table if you let it. The full text is a hover away, and it is
      // already written out in full in the decision list above.
      // A truncating child inside an auto-layout table collapses its cell to
      // the narrowest it can be, so the width is pinned at all three ends.
      <td
        className={pad}
        style={{ width: spec.width ?? 220, minWidth: spec.width ?? 220, maxWidth: spec.width ?? 220 }}
      >
        <div className={`text-[16px] font-semibold ${VERDICT_TONE[v.tone] ?? ''}`}>{v.action}</div>
        <div className="truncate text-[13px] text-muted" title={v.reason}>
          {v.reason}
        </div>
      </td>
    )
  }

  if (spec.id === 'position_on_risk') {
    return (
      <td className={`${pad}`}>
        <RiskScale pnl={view.pnl} />
      </td>
    )
  }

  if (spec.id === 'risk_level') {
    return (
      <td className={`${pad}`}>
        <DangerBadge level={view.risk.level} />
      </td>
    )
  }

  if (spec.id === 'underlying') {
    return (
      <td className={`${pad} sticky left-0 z-10 bg-raised`}>
        {first && <EdgeBar level={view.risk.level} />}
        <div className="text-[17px] font-semibold">{view.strategy.underlying}</div>
        {view.strategy.roll_count > 0 && (
          <div className="text-[12px] text-faint">rolled {view.strategy.roll_count}×</div>
        )}
      </td>
    )
  }

  if (spec.id === 'strategy') {
    /* Undefined risk is this book's normal state, so saying it on every row is
       noise. Only the exception is worth ink. */
    const notes = [
      view.named_name ? view.strategy.strategy_type.toLowerCase() : null,
      view.strategy.risk_profile === 'Defined' ? 'defined risk' : null,
      view.parts > 1 ? `${view.parts} trades` : null,
    ].filter(Boolean)
    return (
      <td className={pad}>
        <div className="truncate text-[17px] font-medium text-ink">
          {view.named_name ?? view.strategy.strategy_type}
        </div>
        {notes.length > 0 && (
          <div className="truncate text-[12px] text-faint">{notes.join(' · ')}</div>
        )}
      </td>
    )
  }

  const numeric = spec.format !== 'text' && spec.format !== 'date' && spec.format !== 'level'
  const emphasis =
    spec.id === 'price' || spec.id === 'open_pnl' ? 'text-[17px] font-semibold' : ''

  return (
    <td
      className={`${pad} ${align} ${numeric ? 'figure' : ''} ${emphasis} ${toneClass(spec, raw)}`}
    >
      {formatField(spec, raw)}
    </td>
  )
}

/* The open-positions table. Sorted by danger first, because the whole point of
   the dashboard is that the thing needing attention is at the top, and danger
   here is always the strategy-level assessment. */
export function StrategyTable({
  views,
  columns,
  catalogue,
  legColumns,
  legCatalogue,
  focus,
  onClearFocus,
}: {
  views: StrategyView[]
  columns: FieldSpec[]
  catalogue: FieldSpec[]
  legColumns: FieldSpec[]
  legCatalogue: FieldSpec[]
  /** A strategy id or a product root to single out, from elsewhere on the page. */
  focus?: string | null
  onClearFocus?: () => void
}) {
  const [expanded, setExpanded] = useState<string | null>(null)
  // null means the default order: what needs a hand first. Any column can take
  // over, because "show me my biggest loser" is a question the table should
  // answer without reading eleven rows.
  const [sort, setSort] = useState<{ id: string; desc: boolean } | null>(null)
  void catalogue
  void legCatalogue

  const sorted = [...views].sort((a, b) => {
    if (sort) {
      const spec = columns.find((c) => c.id === sort.id)
      const av = (a.values ?? {})[sort.id] ?? null
      const bv = (b.values ?? {})[sort.id] ?? null
      const numeric = spec && spec.format !== 'text' && spec.format !== 'date'
      let d: number
      if (sort.id === 'verdict') {
        d = (a.verdict?.rank ?? 9) - (b.verdict?.rank ?? 9)
      } else if (numeric) {
        // A blank sorts last whichever way the column is pointing: an unknown
        // is not the smallest value, it is no value.
        const an = num(av as string | number | null)
        const bn = num(bv as string | number | null)
        if (an === null && bn === null) d = 0
        else if (an === null) return 1
        else if (bn === null) return -1
        else d = an - bn
      } else {
        d = String(av ?? '').localeCompare(String(bv ?? ''))
      }
      if (d !== 0) return sort.desc ? -d : d
    }
    const byVerdict = (a.verdict?.rank ?? 9) - (b.verdict?.rank ?? 9)
    if (byVerdict !== 0) return byVerdict
    const d = rank(b.risk.level) - rank(a.risk.level)
    if (d !== 0) return d
    return b.risk.score - a.risk.score
  })

  // The visible width of the card, which the expanded drawer matches.
  const [measure, paneWidth] = useWidth<HTMLDivElement>()
  const [atStart, setAtStart] = useState(true)
  const [hidden, setHidden] = useState(0)
  const shown = columns

  // How many columns are off the right-hand edge right now: what the button
  // offers to go and get.
  const measureHidden = useCallback(() => {
    const box = findScroller()
    if (!box) return
    const heads = Array.from(box.querySelectorAll('thead th'))
    const edge = box.getBoundingClientRect().right
    setHidden(heads.filter((th) => th.getBoundingClientRect().right > edge + 1).length)
    setAtStart(box.scrollLeft < 8)
  }, [])

  // Deps are counts, not the arrays themselves: `views` is a new array on every
  // render, and an effect that both depends on it and sets state re-runs
  // forever — which detached the scroller's ref often enough that the button
  // found nothing to scroll.
  useEffect(() => {
    measureHidden()
    const box = findScroller()
    if (!box) return
    box.addEventListener('scroll', measureHidden, { passive: true })
    window.addEventListener('resize', measureHidden)
    return () => {
      box.removeEventListener('scroll', measureHidden)
      window.removeEventListener('resize', measureHidden)
    }
  }, [measureHidden, columns.length, views.length])

  /* The rest of the table, fetched rather than dragged for.

     The columns that do not fit are still columns: they belong in their own
     headings beside the numbers they compare against, not restated under each
     row. What was wrong with the scrollbar was never the scrolling, it was
     having to find it and drag it while the ticker column slid out of sight.
     So the button does the scrolling, and the first column is pinned. */
  function slide() {
    // The scroller is found from the button rather than held in a ref. A ref
    // here was reliably null by the time the click arrived -- the table
    // re-renders on every poll, and whatever React was doing with the callback
    // across those renders, the node was not there when it was needed. The
    // button is inside the card; the scroller is the one element in it that
    // scrolls. Nothing to get out of sync.
    const box = findScroller()
    if (!box) return
    const to = atStart ? box.scrollWidth - box.clientWidth : 0
    if (Math.abs(box.scrollLeft - to) < 2) return
    // A jump, not a glide. Animating the columns across reads as the table
    // sliding out from under the cursor; what is wanted is the second half of
    // the row, there, the way turning a page works.
    box.scrollLeft = to
    // The button's own state is set here rather than waiting to hear about the
    // scroll: a scroll event is not guaranteed, and a toggle whose label waits
    // on one can end up pointing the wrong way with no way back.
    setAtStart(!atStart)
    measureHidden()
  }

  function toggleSort(id: string) {
    setSort((old) =>
      old?.id === id ? (old.desc ? null : { id, desc: true }) : { id, desc: false },
    )
  }

  if (sorted.length === 0) {
    return (
      <div className="rounded-card border border-dashed border-line bg-raised px-6 py-12 text-center">
        <div className="text-[16px] text-muted">No open strategies.</div>
        <div className="mt-1 text-[14px] text-faint">
          Positions appear here once a sync has run against your tastytrade account.
        </div>
      </div>
    )
  }

  return (
    // No sheen on this card. The sheen is a gradient across the top of a
    // surface, and the first column is sticky, which means it paints its own
    // flat bg-raised to slide the rest of the table underneath it. Flat cannot
    // match a gradient, so the sticky column read as a differently coloured
    // block wherever the two overlapped — which is the header, the part of the
    // table the eye goes to first.
    <div
      data-positions-card
      className="overflow-hidden rounded-card border border-line bg-raised shadow-[var(--shadow-md)]"
    >
      {/* What the columns mean used to live here, as a strip of four notes
          above the numbers. It crowded the page and still left thirty-six
          fields unexplained; the explanations are on the headings now. What is
          left is the one thing that is a control rather than a note. */}
      {(focus || hidden > 0 || !atStart) && (
        <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2 text-[12px]">
          {(hidden > 0 || !atStart) && (
            <button
              onClick={slide}
              className={`rounded-sm border px-2.5 py-1 uppercase tracking-wider transition-colors ${
                atStart
                  ? 'border-line text-muted hover:bg-hover hover:text-ink'
                  : 'border-accent/50 bg-accent-soft text-accent'
              }`}
            >
              {atStart ? `Show ${hidden} more column${hidden === 1 ? '' : 's'} →` : '← Back to the start'}
            </button>
          )}
          {focus && (
            <button onClick={onClearFocus} className="ml-auto text-accent hover:underline">
              showing {focus} — clear
            </button>
          )}
        </div>
      )}
      <div ref={measure} data-positions-scroller className="overflow-x-auto">
        <table className="w-max min-w-full text-[16px]">
          <thead>
            <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
              {shown.map((c, i) => {
                const on = sort?.id === c.id
                return (
                  <th
                    key={c.id}
                    style={c.width ? { width: c.width, minWidth: c.width } : undefined}
                    className={`py-3.5 font-medium ${i === 0 ? 'pl-4 pr-3' : 'pr-3'} ${
                      c.align === 'right' ? 'text-right' : 'text-left'
                    } ${i === 0 ? 'sticky left-0 z-20 bg-raised' : ''}`}
                  >
                    <Help title={c.label} body={c.help} footer="Click the heading to sort by it.">
                      <button
                        onClick={() => toggleSort(c.id)}
                        className={`inline-flex items-baseline gap-1 uppercase tracking-wider hover:text-ink ${
                          on ? 'text-accent' : ''
                        } ${c.align === 'right' ? 'flex-row-reverse' : ''}`}
                      >
                        <span>{c.label}</span>
                        <span aria-hidden className={on ? 'text-accent' : 'text-transparent'}>
                          {on && sort?.desc ? '↓' : '↑'}
                        </span>
                      </button>
                    </Help>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody className="rows stagger">
            {sorted.map((v) => {
              const s = v.strategy
              const isOpen = expanded === s.id
              const calledAway = num(v.pnl.called_away)
              // Focus arrives either as a strategy id (from the decision list)
              // or a product root (from the exposure table).
              const focused =
                !!focus && (focus === s.id || s.underlying.toUpperCase().startsWith(focus))
              return (
                <Fragment key={s.id}>
                  <tr
                    onClick={() => setExpanded(isOpen ? null : s.id)}
                    className={`cursor-pointer border-b border-line/60 transition-colors hover:bg-hover ${
                      focused ? 'bg-accent-soft' : ''
                    }`}
                  >
                    {shown.map((c, i) => (
                      <Cell key={c.id} spec={c} view={v} first={i === 0} />
                    ))}
                  </tr>

                  {isOpen && (
                    <tr key={`${s.id}-detail`} className="border-b border-line/60 bg-sunken">
                      {/* The table is wider than the window — that is what the
                          horizontal scrollbar is for — and the drawer used to
                          inherit that width, which put the risk panel off the
                          right-hand edge where it could not be read without
                          scrolling away from the row it belonged to. Stuck to
                          the left of the scroller at exactly the visible
                          width, it stays where it can be read. */}
                      <td colSpan={shown.length} className="p-0">
                        <div
                          /* minmax(0,…), not 1fr: a `1fr` track refuses to go below
                              its content's minimum, and the payoff chart is an SVG with
                              an explicit pixel width, so the column grew to fit the
                              chart and shouldered the risk panel off the edge. */
                          className="sticky left-0 grid gap-3 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_360px]"
                          style={paneWidth ? { width: paneWidth } : undefined}
                        >
                          <div className="space-y-3">
                            <LegDetail view={v} columns={legColumns} />
                            <PayoffPanel strategyId={s.id} />
                          </div>

                          <div className="rounded-card border border-line bg-raised p-3">
                            <div className="mb-2 text-[13px] font-medium uppercase tracking-wider text-faint">
                              Why this risk level
                            </div>
                            {v.risk.reasons.length === 0 ? (
                              <div className="text-[14px] text-muted">
                                Nothing flagged. The position is inside every threshold.
                              </div>
                            ) : (
                              <ul className="space-y-1.5">
                                {v.risk.reasons.map((r) => (
                                  <li key={r.code} className="flex gap-2 text-[14px]">
                                    <DangerBadge level={r.level} className="shrink-0" />
                                    <span className="text-muted">{r.message}</span>
                                  </li>
                                ))}
                              </ul>
                            )}

                            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 border-t border-line pt-3 text-[14px]">
                              {/* A covered short has an answer even when max
                                  profit and max loss do not: assignment sells
                                  at the short strike, the cover buys at its
                                  own, and the difference is exact. On a
                                  diagonal that is the only one of the three
                                  that is knowable, so it takes their place
                                  rather than sitting under three dashes. */}
                              {calledAway !== null ? (
                                <>
                                  <dt className="text-faint">If called away</dt>
                                  <dd
                                    className={`figure text-right font-medium ${
                                      calledAway >= 0 ? 'text-profit' : 'text-loss'
                                    }`}
                                  >
                                    {money(v.pnl.called_away, { sign: true, cents: false })}
                                  </dd>
                                </>
                              ) : (
                                <>
                                  <dt className="text-faint">Max profit</dt>
                                  <dd className="num text-right">
                                    {money(v.pnl.max_profit, { cents: false })}
                                  </dd>
                                  <dt className="text-faint">Max loss</dt>
                                  <dd className="num text-right">
                                    {v.pnl.max_loss !== null ? (
                                      money(v.pnl.max_loss, { cents: false })
                                    ) : s.is_multi_expiration ? (
                                      EM_DASH
                                    ) : (
                                      <span className="text-muted">undefined</span>
                                    )}
                                  </dd>
                                  <dt className="text-faint">% of max loss</dt>
                                  <dd className="num text-right">{pct(v.pnl.pct_of_max_loss, 1)}</dd>
                                </>
                              )}
                              <dt className="text-faint">Distance to short</dt>
                              <dd className="num text-right">{pct(v.risk.distance_to_short_pct, 1)}</dd>
                              <dt className="text-faint">In sigma</dt>
                              <dd className="num text-right">
                                {v.risk.distance_to_short_sigma === null
                                  ? EM_DASH
                                  : `${decimals(v.risk.distance_to_short_sigma, 2)}σ`}
                              </dd>
                              <dt className="text-faint">IV rank now</dt>
                              <dd className="num text-right">{pct(v.iv_rank, 0)}</dd>
                            </dl>

                            {/* A refusal with a reason, and only where the
                                refusal is all there is to say. */}
                            {s.is_multi_expiration && calledAway === null && (
                              <p className="mt-2.5 text-[13px] leading-relaxed text-tested">
                                These legs expire on different days. While the later one still has
                                time value, max profit and max loss cannot be worked out from the
                                strikes, so they are left blank rather than guessed.
                              </p>
                            )}

                            {v.pnl.quoted_legs < v.pnl.total_legs && (
                              <div className="mt-3 rounded-sm border border-watch/30 bg-watch-soft px-2 py-3.5 text-[13px] text-watch">
                                Only {v.pnl.quoted_legs} of {v.pnl.total_legs} legs are quoted, so P&amp;L is
                                incomplete. Nothing has been guessed.
                              </div>
                            )}
                          </div>
                        </div>
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
