import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useWidth } from '../lib/useMeasure'
import { Help } from './Help'
import { DangerBadge } from './DangerBadge'
import { Estimate } from './Estimate'
import { ExpectedRange } from './ExpectedRange'
import { RiskScale } from './RiskScale'
import { LegDetail } from './LegDetail'
import { RollChain } from './RollChain'
import { PayoffPanel } from './PayoffPanel'
import { money, pct, decimals, num, EM_DASH } from '../lib/format'
import { formatField, summarise, toneClass, type FieldSpec } from '../lib/fields'
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
  // 20px between columns, not 12. Credit, P&L and P&L today are all the width
  // of their own numbers — their headings are shorter than their figures — so
  // at a 12px gutter three seven-digit numbers ran together as one block while
  // the columns with long headings sat in space of their own. The gutter is
  // what makes a row of figures read as a row.
  const pad = first ? 'py-3.5 pl-4 pr-5' : 'py-3.5 pr-5'
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
      {/* A P&L built on a model price says so, right where it is read. */}
      {spec.id === 'open_pnl' && view.strategy.legs.some((l) => l.mark_estimated) && (
        <Estimate compact />
      )}
    </td>
  )
}

function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[14px] text-muted">{label}</dt>
      <dd className="num mt-0.5 text-[17px] text-ink">{children}</dd>
    </div>
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
  onLegColumns,
  focus,
  onClearFocus,
}: {
  views: StrategyView[]
  columns: FieldSpec[]
  catalogue: FieldSpec[]
  legColumns: FieldSpec[]
  legCatalogue: FieldSpec[]
  onLegColumns?: (ids: string[]) => void | Promise<void>
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
  // Empty space kept at the right edge so the clip lands between two columns.
  const [trim, setTrim] = useState(0)
  const frameRef = useRef<HTMLDivElement>(null)
  /* A column with nothing in it, on every row, is not information — it is a
     heading with a ruler under it. Some of them can never have a value for the
     book as it stands: max loss on a page of undefined-risk strangles, IV rank
     at entry on positions opened before the app was recording it. Rather than
     print a column of dashes, the table leaves them out and the Customise
     panel still lists them, so choosing one and seeing nothing happen is the
     one confusion this could cause — which is why it says so there.

     Drawn columns are kept whatever they hold: the risk badge and the verdict
     render from the view rather than from a value. */
  // Whether every row is one product — the one case where a raw delta adds up.
  const oneProduct =
    new Set(views.map((v) => v.strategy.underlying.replace(/[FGHJKMNQUVXZ]\d$/, ''))).size === 1
  const shown = columns.filter(
    (c) =>
      c.id === 'verdict' ||
      c.id === 'position_on_risk' ||
      views.some((v) => (v.values ?? {})[c.id] !== null && (v.values ?? {})[c.id] !== undefined),
  )

  /* How many columns are off the right-hand edge, and how much of the card to
     leave empty so that none of them is half-shown.

     A column that straddles the edge used to be sliced down the middle, which
     put half a DANGER badge against the border and read as a rendering fault
     rather than as a row that continues. The window is the width it is, so the
     fix is not to fit more in: it is to stop the clip mid-column. The scroller
     is pulled in by the width of the sliver, which lands its edge exactly on
     the boundary between two columns.

     Measured against the card, never against the scroller. Measuring the
     scroller after it has been pulled in finds no straddler, sets the trim
     back to zero, and the edge flickers between the two states forever. */
  const measureHidden = useCallback(() => {
    const box = findScroller()
    const card = frameRef.current
    if (!box || !card) return
    const heads = Array.from(box.querySelectorAll('thead th'))
    const edge = card.getBoundingClientRect().right
    setHidden(heads.filter((th) => th.getBoundingClientRect().right > edge + 1).length)
    setAtStart(box.scrollLeft < 8)

    const straddler = heads.find((th) => {
      const r = th.getBoundingClientRect()
      return r.left < edge - 1 && r.right > edge + 1
    })
    const sliver = straddler ? edge - straddler.getBoundingClientRect().left : 0
    // A column wider than a quarter of the card would leave more empty space
    // than the cut is worth; there the fade does the work instead.
    const room = card.getBoundingClientRect().width
    setTrim(sliver > 0 && sliver < room * 0.25 ? Math.floor(sliver) : 0)
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
    const heads = Array.from(box.querySelectorAll<HTMLElement>('thead th'))
    const frame = box.getBoundingClientRect()
    const limit = box.scrollWidth - box.clientWidth

    // Land on a column, not on a pixel. Jumping by the width of the window
    // sliced whichever column straddled the edge, which is the one thing a
    // page turn must not do: the column that was half-visible is the first one
    // you read next. The last page lands flush against the end, so nothing is
    // cut there either.
    const sticky = heads[0]?.getBoundingClientRect().width ?? 0
    const cut = heads.find((th) => th.getBoundingClientRect().right > frame.right - 1)
    const to =
      !cut || box.scrollLeft >= limit - 2
        ? 0
        : Math.min(limit, box.scrollLeft + (cut.getBoundingClientRect().left - frame.left - sticky))

    if (Math.abs(box.scrollLeft - to) < 2) return
    // A jump, not a glide. Animating the columns across reads as the table
    // sliding out from under the cursor; what is wanted is the rest of the
    // row, there, the way turning a page works.
    box.scrollLeft = to
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
          {focus && (
            <button onClick={onClearFocus} className="text-accent hover:underline">
              showing {focus} — clear
            </button>
          )}
          {(hidden > 0 || !atStart) && (
            <button
              onClick={slide}
              /* On the right, where the columns it fetches are. It sat on the
                 left, pointing at an edge two feet away from the one it
                 moves. */
              className={`ml-auto rounded-sm border px-2.5 py-1 uppercase tracking-wider transition-colors ${
                hidden > 0
                  ? 'border-line text-muted hover:bg-hover hover:text-ink'
                  : 'border-accent/50 bg-accent-soft text-accent'
              }`}
            >
              {hidden > 0 ? `Show ${hidden} more column${hidden === 1 ? '' : 's'} →` : '← Back to the start'}
            </button>
          )}
        </div>
      )}
      {/* A column that straddles the edge cannot be avoided -- the window is
          the width it is -- but it can stop looking broken. The fade says the
          row continues, the button says how far, and the page turn puts that
          same column first. */}
      <div ref={frameRef} className="relative">
        {hidden > 0 && expanded === null && (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 z-30"
            style={{
              width: trim + 48,
              background: 'linear-gradient(to right, transparent, var(--bg-raised))',
            }}
          />
        )}
      <div
        ref={measure}
        data-positions-scroller
        className="overflow-x-auto"
        // No trim while a drawer is open: the drawer is sized to the full
        // visible width, and a scroller pulled in under it clipped its right
        // edge — the risk panel's numbers ran off the side.
        style={{ marginRight: expanded === null ? trim : 0 }}
      >
        <table className="w-max min-w-full text-[16px]">
          <thead>
            <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
              {shown.map((c, i) => {
                const on = sort?.id === c.id
                return (
                  <th
                    key={c.id}
                    style={
                      c.width
                        ? { width: c.width, minWidth: c.width }
                        : // A floor under the numeric columns so a short value
                          // in a short-headed column is not squeezed against
                          // its neighbour.
                          c.align === 'right'
                          ? { minWidth: 92 }
                          : undefined
                    }
                    className={`py-3.5 font-medium ${i === 0 ? 'pl-4 pr-5' : 'pr-5'} ${
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
            {/* The book, added up. Every figure here is a column of this table
              summed down its own length — no new arithmetic, nothing weighted
              or averaged. A column that does not add up (a strike, a date, one
              trade's percentage of its own credit) is left empty rather than
              given a number that would only look like one. */}
          {sorted.length > 1 && (
            
              <tr className="border-b-2 border-line-strong bg-raised text-[16px]">
                {shown.map((c, i) => {
                  const total = summarise(
                    c,
                    sorted.map((v) => ((v.values ?? {})[c.id] ?? null) as never),
                    'book',
                    oneProduct,
                  )
                  return (
                    <td
                      key={c.id}
                      className={`pb-3 pt-1 ${i === 0 ? 'pl-4 pr-5' : 'pr-5'} ${
                        c.align === 'right' ? 'text-right' : 'text-left'
                      } ${
                        i === 0 ? 'sticky left-0 z-10 bg-raised' : ''
                      } ${total === null ? '' : `num font-medium ${toneClass(c, total)}`}`}
                    >
                      {i === 0 ? (
                        <span className="label text-[13px]">
                          All {sorted.length} positions
                        </span>
                      ) : total === null ? (
                        ''
                      ) : (
                        formatField(c, total)
                      )}
                    </td>
                  )
                })}
              </tr>
            
          )}
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
                          className="sticky left-0 space-y-3 px-4 py-3"
                          style={paneWidth ? { width: paneWidth } : undefined}
                        >
                          {/* Full-width rows, not two columns. Side by side,
                              the risk panel grew with every reason it had to
                              give, and a position with four reasons stood a
                              tall narrow panel beside a screen of nothing.
                              Rows cannot leave a hole: each one is as tall as
                              what is in it. */}
                          <RollChain strategy={s} />
                          <LegDetail
                            view={v}
                            columns={legColumns}
                            catalogue={legCatalogue}
                            onColumns={onLegColumns}
                          />

                          <div className="rounded-card border border-line bg-raised p-4">
                            <div className="mb-3 text-[14px] font-medium uppercase tracking-wider text-muted">
                              Why this risk level
                            </div>
                            {v.risk.reasons.length === 0 ? (
                              <div className="text-[16px] text-ink">
                                Nothing flagged. The position is inside every threshold.
                              </div>
                            ) : (
                              <ul className="grid gap-x-6 gap-y-2.5 xl:grid-cols-2">
                                {v.risk.reasons.map((r) => (
                                  <li key={r.code} className="flex items-start gap-2.5 text-[16px] leading-relaxed">
                                    <DangerBadge level={r.level} className="shrink-0" />
                                    <span className="text-ink">{r.message}</span>
                                  </li>
                                ))}
                              </ul>
                            )}

                            <ExpectedRange view={v} wide />

                            {/* The numbers as a strip of tiles, label over
                                figure, so they sit in one line across the
                                width instead of a tall two-column list. */}
                            <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-line pt-4 sm:grid-cols-3 xl:grid-cols-6">
                              {/* A covered short has an answer even when max
                                  profit and max loss do not: assignment sells
                                  at the short strike, the cover buys at its
                                  own, and the difference is exact. */}
                              {calledAway !== null ? (
                                <Tile label="If called away">
                                  <span className={calledAway >= 0 ? 'text-profit' : 'text-loss'}>
                                    {money(v.pnl.called_away, { sign: true, cents: false })}
                                  </span>
                                </Tile>
                              ) : (
                                <>
                                  <Tile label="Max profit">{money(v.pnl.max_profit, { cents: false })}</Tile>
                                  <Tile label="Max loss">
                                    {v.pnl.max_loss !== null ? (
                                      money(v.pnl.max_loss, { cents: false })
                                    ) : s.is_multi_expiration ? (
                                      EM_DASH
                                    ) : (
                                      <span className="text-muted">undefined</span>
                                    )}
                                  </Tile>
                                  <Tile label="% of max loss">{pct(v.pnl.pct_of_max_loss, 1)}</Tile>
                                </>
                              )}
                              {/* In the underlying's own units first, because
                                  the expected move beside it is in those
                                  units too. */}
                              <Tile label="Room to the short">
                                {(() => {
                                  const away = num(v.risk.distance_to_short_pct)
                                  const price = num(v.underlying_price)
                                  return away === null
                                    ? EM_DASH
                                    : price === null
                                      ? pct(away, 1)
                                      : `${decimals(away * price, 2)} (${pct(away, 1)})`
                                })()}
                              </Tile>
                              <Tile label="Expected move">
                                {v.risk.expected_move === null
                                  ? EM_DASH
                                  : `±${decimals(v.risk.expected_move, 2)}`}
                              </Tile>
                              <Tile label="IV rank now">{pct(v.iv_rank, 0)}</Tile>
                            </dl>

                            {/* A refusal with a reason, and only where the
                                refusal is all there is to say. */}
                            {s.is_multi_expiration && calledAway === null && (
                              <p className="mt-3 text-[15px] leading-relaxed text-tested">
                                These legs expire on different days. While the later one still has
                                time value, max profit and max loss cannot be worked out from the
                                strikes, so they are left blank rather than guessed.
                              </p>
                            )}

                            {v.pnl.quoted_legs < v.pnl.total_legs && (
                              <div className="mt-3 rounded-sm border border-watch/30 bg-watch-soft px-2.5 py-3 text-[15px] leading-relaxed text-watch">
                                Only {v.pnl.quoted_legs} of {v.pnl.total_legs} legs are quoted, so P&amp;L is
                                incomplete. Nothing has been guessed.
                              </div>
                            )}
                          </div>

                          <PayoffPanel strategyId={s.id} />
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
    </div>
  )
}
