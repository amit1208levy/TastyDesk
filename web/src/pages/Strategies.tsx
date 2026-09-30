import { createContext, useContext, useMemo, useState } from 'react'
import { RollChain, RolledTag } from '../components/RollChain'
import { PositionDetail } from '../components/PositionDetail'
import type { FieldSpec } from '../lib/fields'
import { DangerBadge } from '../components/DangerBadge'
import { EquityCurve } from '../components/EquityCurve'
import { ErrorPanel, Loading, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, shortDate, num, decimals, dteLabel } from '../lib/format'
import { equityCurve, openNow, statsOf } from '../lib/stats'
import { DANGER_ORDER } from '../types'
import type {
  LivePosition,
  LiveStrategy,
  MatchReport,
  NamedMember,
  NamedStrategy,
  StrategyMatch,
  StrategyView,
} from '../types'

const ENDING: Record<NamedMember['ending'], { label: string; cls: string }> = {
  open: { label: 'open', cls: 'border-accent/40 bg-accent-soft text-accent' },
  closed: { label: 'closed', cls: 'border-line bg-sunken text-muted' },
  expired: { label: 'expired', cls: 'border-line bg-sunken text-muted' },
  assigned: { label: 'assigned', cls: 'border-warn/40 bg-sunken text-warn' },
  unverified: { label: 'unverified', cls: 'border-warn/40 bg-sunken text-warn' },
}

const VERDICT_TONE: Record<string, string> = {
  act: 'text-loss',
  take: 'text-profit',
  watch: 'text-tested',
  none: 'text-faint',
}

type Row = NamedMember & Partial<StrategyMatch> & { yours?: boolean }

// A delta, signed, to two places.
function signed(v: string): string {
  const n = num(v) ?? 0
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}`
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[12px] uppercase tracking-wider text-faint">{label}</div>
      <div className={`num text-[16px] font-medium ${tone ?? ''}`}>{value}</div>
    </div>
  )
}

/* One figure in the "now" strip: the thing the page is for, set large enough
   to read across a desk. A missing value prints an em dash — an unpriced leg
   is not a zero. */
function Now({ label, value, tone, note }: { label: string; value: string; tone?: string; note?: string }) {
  return (
    <div className="min-w-[7.5rem]">
      <div className="text-[12px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`figure text-[24px] font-semibold leading-tight ${tone ?? ''}`}>{value}</div>
      {note && <div className="text-[13px] text-muted">{note}</div>}
    </div>
  )
}

/* One open position, now. Everything here is a number that moves: what it is
   worth, which way it leans, how much time is left and how close the market
   has come to the short strike. The trade's own history is one line at the
   bottom, because at this point it is context rather than the question. */
/* What an open position needs to draw its full detail — the same panel the
   Positions page opens — handed down rather than fetched per card. */
const DetailContext = createContext<{
  views: Map<string, StrategyView>
  legColumns: FieldSpec[]
  legCatalogue?: FieldSpec[]
  onLegColumns?: (ids: string[]) => Promise<void>
}>({ views: new Map(), legColumns: [] })

function pickFields(catalogue: FieldSpec[] | undefined, chosen: string[] | undefined): FieldSpec[] {
  if (!catalogue) return []
  const byId = new Map(catalogue.map((f) => [f.id, f]))
  const ids = chosen?.length ? chosen : catalogue.filter((f) => f.default).map((f) => f.id)
  return ids.map((id) => byId.get(id)).filter((f): f is FieldSpec => f !== undefined)
}

/* One sentence on an open position, from its own numbers. */
function summarise(p: LivePosition): string {
  const parts: string[] = []
  const pnl = num(p.open_pnl)
  const share = num(p.pct_of_credit)
  if (pnl !== null) {
    parts.push(
      `${pnl >= 0 ? 'Up' : 'Down'} ${money(Math.abs(pnl), { cents: false })}` +
        (share !== null ? ` (${pct(share, 0, true)} of credit)` : ''),
    )
  }
  const price = num(p.underlying_price)
  const away = num(p.distance_pct)
  const move = num(p.expected_move)
  if (p.breached) {
    parts.push(`price is through your short ${p.breached_side ?? ''} strike`.replace('  ', ' '))
  } else if (away !== null && price !== null) {
    const points = away * price
    const reach = move !== null && move >= points
    parts.push(
      `nearest short strike ${decimals(points, price >= 1000 ? 0 : 2)} away (${pct(away, 1)})` +
        (move !== null ? `, ${reach ? 'within' : 'outside'} the ±${decimals(move, price >= 1000 ? 0 : 2)} the market expects` : ''),
    )
  }
  if (p.dte !== null) parts.push(`${p.dte} day${p.dte === 1 ? '' : 's'} left`)
  const theta = num(p.theta)
  if (theta !== null && Math.abs(theta) >= 1) {
    parts.push(
      theta > 0
        ? `time pays you ${money(theta, { cents: false })} a day`
        : `time costs you ${money(-theta, { cents: false })} a day`,
    )
  }
  if (parts.length === 0) return 'Not priced right now.'
  const line = parts.join('; ')
  return line.charAt(0).toUpperCase() + line.slice(1) + '.'
}

function LiveRow({ p, expanded = false }: { p: LivePosition; expanded?: boolean }) {
  const detail = useContext(DetailContext)
  const view = detail.views.get(p.id)
  const [open, setOpen] = useState(expanded)
  const theta = num(p.theta)

  return (
    <li className="rounded-card border border-line bg-sunken px-4 py-3.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <DangerBadge level={p.risk_level} />
        <span className="text-[18px] font-semibold">{p.underlying}</span>
        <span className="text-[15px] text-muted">{p.structure}</span>
        {p.parts > 1 && <span className="text-[13px] text-faint">{p.parts} trades as one</span>}
        <span className="num ml-auto text-[15px] text-ink">
          {dteLabel(p.dte)} <span className="text-muted">left</span>
        </span>
      </div>

      {p.verdict && (
        <div className="mt-2 text-[16px]">
          <span className={`font-semibold ${VERDICT_TONE[p.verdict.tone] ?? ''}`}>
            {p.verdict.action}
          </span>
          <span className="text-muted"> — {p.verdict.reason}</span>
        </div>
      )}

      <div className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
        <Now
          label="P&L"
          value={money(p.open_pnl, { sign: true, cents: false })}
          tone={(num(p.open_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'}
          note={p.pct_of_credit === null ? undefined : `${pct(p.pct_of_credit, 0, true)} of credit`}
        />
        <Now
          label="Today"
          value={money(p.day_change, { sign: true, cents: false })}
          tone={(num(p.day_change) ?? 0) >= 0 ? 'text-profit' : 'text-loss'}
        />
        <Now
          label="Delta"
          value={p.net_delta === null ? '—' : signed(p.net_delta)}
          note={p.underlying.startsWith('/') ? 'contracts' : 'shares'}
        />
        <Now
          label="BWD"
          value={p.bwd === null ? '—' : signed(p.bwd)}
          note="in SPY shares"
        />
        <Now
          label="Theta"
          value={theta === null ? '—' : money(theta, { sign: true, cents: false })}
          note="a day"
        />
        <Now
          label="Underlying"
          value={p.underlying_price === null ? '—' : decimals(p.underlying_price, 2)}
          note={p.iv_rank === null ? undefined : `IV rank ${pct(p.iv_rank, 0)}`}
        />
      </div>

      {/* What is going on with it, in one line: how it stands, where price is
          against the strikes, how long is left, and what time is doing. */}
      <div className="mt-2 text-[16px] leading-relaxed text-ink">{summarise(p)}</div>

      <div className="mt-1.5 text-[13px] text-muted">
        Opened {shortDate(p.opened)}
        {p.days_held !== null && `, held ${p.days_held}d`}
        {p.dte_at_entry !== null && ` from ${p.dte_at_entry} DTE`}
        {p.bp !== null && ` · holding ${money(p.bp, { cents: false })} of buying power`}
      </div>

      {/* The full picture, as on the Positions page: legs, why this risk
          level, where it can get to by expiry, and the payoff. */}
      {view && (
        <>
          <button
            onClick={() => setOpen(!open)}
            className={`mt-3 rounded-sm border px-3 py-1.5 text-[14px] transition-colors ${
              open
                ? 'border-accent/50 bg-accent-soft text-accent'
                : 'border-line text-muted hover:bg-hover hover:text-ink'
            }`}
          >
            {open ? 'Hide details ▴' : 'Legs, risk, range and payoff ▾'}
          </button>
          {open && (
            <div className="mt-3">
              <PositionDetail
                view={view}
                legColumns={detail.legColumns}
                legCatalogue={detail.legCatalogue}
                onLegColumns={detail.onLegColumns}
              />
            </div>
          )}
        </>
      )}
    </li>
  )
}

/* The head of the card: what the strategy is carrying, before any history.

   A strategy with nothing open says so in one line rather than showing an
   empty strip of dashes — there is nothing to watch, and the page should not
   pretend otherwise. */
function LivePanel({ live, expanded = false }: { live: LiveStrategy; expanded?: boolean }) {
  if (live.count === 0) {
    return (
      <div className="mt-3 border-t border-line pt-3 text-[15px] text-muted">
        Nothing open in this strategy right now. Its record is under Performance.
      </div>
    )
  }

  const partial = live.positions.some((p) => p.open_pnl === null)
  // With one position open, the totals strip is that position said twice. The
  // row below carries every figure in it, so the card goes straight there.
  const single = live.count === 1

  return (
    <div className="mt-3 border-t border-line pt-3">
      {!single && (
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-7">
        <Now
          label="Open P&L"
          value={money(live.open_pnl, { sign: true, cents: false })}
          tone={(num(live.open_pnl) ?? 0) >= 0 ? 'text-profit' : 'text-loss'}
          note="a mark, not a result"
        />
        <Now
          label="Today"
          value={money(live.day_change, { sign: true, cents: false })}
          tone={(num(live.day_change) ?? 0) >= 0 ? 'text-profit' : 'text-loss'}
        />
        <Now
          label="Delta"
          value={live.net_delta === null ? '—' : signed(live.net_delta)}
          note={live.positions[0]?.underlying.startsWith('/') ? 'contracts' : 'shares'}
        />
        <Now
          label="BWD"
          value={live.bwd === null ? '—' : signed(live.bwd)}
          note="in SPY shares"
        />
        <Now
          label="Theta"
          value={money(live.theta, { sign: true, cents: false })}
          note="a day"
        />
        <Now label="Nearest expiry" value={dteLabel(live.dte)} note={`${live.count} open`} />
        <Now label="Buying power" value={money(live.bp, { cents: false })} note="held by it" />
      </div>
      )}
      {partial && (
        <p className="mt-1.5 text-[14px] text-warn">
          One of these has no price yet, so the totals above are left blank rather than shown
          short.
        </p>
      )}

      <ul className={`${single ? '' : 'mt-3'} space-y-2.5`}>
        {live.positions.map((p) => (
          <LiveRow key={p.id} p={p} expanded={expanded} />
        ))}
      </ul>
    </div>
  )
}
/* One trade in a strategy's history.

   A row the app matched rather than the user grouped says so, says how sure it
   is, and opens to show what that confidence was built on — the agreements, the
   disagreements, and what could not be checked at all. */
function HistoryRow({
  r,
  onAdopt,
  onDrop,
  busy,
}: {
  r: Row
  onAdopt: () => void
  onDrop: () => void
  busy: boolean
}) {
  const [open, setOpen] = useState(false)
  const [rollsOpen, setRollsOpen] = useState(false)
  const pnl = r.is_open ? r.open_pnl : r.realized_pnl
  const value = num(pnl)
  const credit = num(r.credit)
  const share = value !== null && credit ? value / Math.abs(credit) : null
  const end = ENDING[r.ending]
  const confidence = r.confidence ?? null

  return (
    <>
      <tr
        className={`cursor-pointer border-t border-line align-top transition-colors hover:bg-hover ${
          r.is_open ? 'bg-accent-soft/40' : ''
        }`}
        onClick={() => setOpen(!open)}
      >
        {/* Status: open or how it ended, and its rolls. */}
        <td
          className={`whitespace-nowrap py-3.5 pl-4 pr-4 ${
            r.is_open ? 'border-l-4 border-l-accent' : 'border-l-4 border-l-transparent'
          }`}
        >
          <span
            className={`inline-block rounded-sm px-2 py-0.5 text-[12px] font-semibold uppercase tracking-wide ${
              r.is_open ? 'bg-accent text-white' : `border ${end.cls}`
            }`}
          >
            {end.label}
          </span>
          {r.roll_count > 0 && (
            <div className="mt-1.5">
              <RolledTag count={r.roll_count} shown={rollsOpen} onClick={() => setRollsOpen(!rollsOpen)} />
            </div>
          )}
        </td>

        {/* What it is, and whether you put it here or the app matched it. */}
        <td className="whitespace-nowrap py-3.5 pr-4">
          <div className="text-[16px] font-semibold text-ink">{r.underlying}</div>
          <div className="text-[13px] text-muted">{r.structure}</div>
          <div className="mt-0.5 text-[12px]">
            {r.yours ? (
              <span className="text-accent">in this strategy</span>
            ) : (
              <span className="text-tested" title="How sure the app is that this trade belongs here">
                matched{confidence === null ? '' : ` · ${pct(confidence, 0)} sure`}
              </span>
            )}
          </div>
        </td>

        <td className="whitespace-nowrap py-3.5 pr-4">
          <div className="text-ink">
            {shortDate(r.opened)} → {r.is_open ? <span className="font-medium text-accent">now</span> : shortDate(r.closed)}
          </div>
          <div className="text-[13px] text-muted">
            {r.days_held === null ? '' : `held ${r.days_held} day${r.days_held === 1 ? '' : 's'}`}
          </div>
        </td>

        {/* One leg per line, in words. */}
        <td className="py-3.5 pr-4 text-[14px] text-ink">
          {r.legs.map((leg, i) => (
            <div key={i} className="whitespace-nowrap leading-relaxed">
              <span className={leg.startsWith('short') ? 'font-medium text-accent' : 'text-muted'}>
                {leg.split(' ')[0]}
              </span>{' '}
              {leg.split(' ').slice(1).join(' ')}
            </div>
          ))}
        </td>

        <td className="num whitespace-nowrap py-3.5 pr-4 text-right">
          <div className="text-ink">
            {r.is_open
              ? r.dte_now === null ? '—' : `${r.dte_now} left`
              : r.dte_at_close === null ? '—' : `${r.dte_at_close} at close`}
          </div>
          <div className="text-[13px] text-muted">
            {r.dte_at_entry === null ? '' : `${r.dte_at_entry} at open`}
          </div>
        </td>

        <td className="num whitespace-nowrap py-3.5 pr-4 text-right">
          <div className="text-ink">{credit === null ? '—' : money(Math.abs(credit), { cents: false })}</div>
          <div className="text-[13px] text-muted">
            {credit === null ? '' : credit >= 0 ? 'credit taken in' : 'debit paid'}
          </div>
        </td>

        <td className="num whitespace-nowrap py-3.5 pr-4 text-right">
          <div
            className={`text-[17px] font-semibold ${
              value === null ? 'text-faint' : value >= 0 ? 'text-profit' : 'text-loss'
            }`}
          >
            {pnl === null ? '—' : money(pnl, { sign: true, cents: false })}
          </div>
          <div className="text-[13px] text-muted">
            {share !== null && Math.abs(share) < 10
              ? `${pct(share, 0, true)} of ${credit !== null && credit < 0 ? 'cost' : 'credit'} · `
              : ''}
            {r.is_open ? 'open, not banked' : 'banked'}
          </div>
        </td>

        <td className="py-3.5 pr-3 text-right">
          <button
            onClick={(e) => {
              e.stopPropagation()
              if (r.yours) onDrop()
              else onAdopt()
            }}
            disabled={busy}
            title={r.yours ? 'Remove this trade from the strategy' : 'Keep this one whatever the slider says'}
            className="rounded-sm px-1.5 text-[15px] text-muted hover:bg-hover hover:text-ink disabled:opacity-40"
          >
            {r.yours ? '×' : '+'}
          </button>
        </td>
      </tr>

      {rollsOpen && (
        <tr className="bg-sunken">
          <td colSpan={8} className="px-3 py-3">
            <RollChain
              strategyId={r.id}
              rolls={r.rolls ?? []}
              rollCount={r.roll_count}
              holding={r.is_open ? r.legs : null}
              onClose={() => setRollsOpen(false)}
            />
          </td>
        </tr>
      )}

      {open && !r.yours && (
        <tr className="border-t border-line/40 bg-sunken">
          <td colSpan={8} className="px-4 py-3 text-[13px]">
            <div className="flex flex-wrap gap-x-5 gap-y-1">
              {(r.reasons ?? []).length > 0 && (
                <span className="text-profit">✓ {(r.reasons ?? []).join(' · ')}</span>
              )}
              {(r.misses ?? []).length > 0 && (
                <span className="text-loss">✗ {(r.misses ?? []).join(' · ')}</span>
              )}
              {(r.unknowns ?? []).length > 0 && (
                <span className="text-warn">? {(r.unknowns ?? []).join(' · ')}</span>
              )}
              {(r.not_applicable ?? []).length > 0 && (
                <span className="text-faint">– {(r.not_applicable ?? []).join(' · ')}</span>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/* One named strategy.

   The order of this card is the argument it makes. A strategy is something he
   is running, not a folder of receipts, so it opens with what it is carrying
   today — the verdict, the delta, the time left, how close the market has come
   to a short strike — and the history sits underneath it, summarised in a line
   and opened only when he asks for it. The record is what the idea has done;
   the top of the card is what it is doing. */
function StrategyCard({
  strategy,
  report,
  threshold,
  onChange,
}: {
  strategy: NamedStrategy
  report: MatchReport | undefined
  threshold: number
  onChange: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)
  // Two views of one strategy: what it is carrying now, and how it has done.
  // A strategy with something open starts on the first.
  const [tab, setTab] = useState<'current' | 'performance'>(
    strategy.live.count > 0 ? 'current' : 'performance',
  )
  const showPast = tab === 'performance'

  const rows = useMemo<Row[]>(() => {
    const mine: Row[] = strategy.members.map((m) => ({ ...m, yours: true }))
    const matched: Row[] = (report?.candidates ?? [])
      .filter((c) => c.confidence >= threshold)
      .map((c) => ({ ...c, yours: false }))
    // What is open comes first, always; newest first inside each group.
    return [...mine, ...matched].sort((a, b) => {
      if (a.is_open !== b.is_open) return a.is_open ? -1 : 1
      return a.opened < b.opened ? 1 : -1
    })
  }, [strategy.members, report, threshold])

  const stats = useMemo(() => statsOf(rows), [rows])
  const curve = useMemo(() => equityCurve(rows), [rows])
  // Only for the strategies with nothing live: the open figure there comes
  // from the matched rows rather than from a priced position.
  const running = useMemo(() => openNow(rows), [rows])

  const nearMisses = useMemo(
    () =>
      (report?.candidates ?? [])
        .filter((c) => c.confidence < threshold && c.confidence >= threshold - 0.2)
        .slice(0, 12),
    [report, threshold],
  )

  async function adopt(tradeId: string) {
    setBusy(tradeId)
    try {
      await api.adoptMatches(strategy.id, [tradeId])
      onChange()
    } finally {
      setBusy(null)
    }
  }

  async function drop(tradeId: string) {
    setBusy(tradeId)
    try {
      await api.dropMember(strategy.id, tradeId)
      onChange()
    } finally {
      setBusy(null)
    }
  }

  const matchedCount = rows.length - strategy.members.length
  const live = strategy.live

  return (
    <div className="lift sheened rounded-card border border-line bg-raised p-5 shadow-[var(--shadow-md)]">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="display text-[21px]">{strategy.name}</h3>
        <span className="rounded-full border border-line px-1.5 py-0.5 text-[12px] text-muted">
          {strategy.product}
        </span>
        <span className="text-[14px] text-faint">{strategy.shape}</span>
        {live.count > 0 && (
          <span className="text-[14px] text-accent">
            {live.count} open now
          </span>
        )}
        <span className="ml-auto text-[13px] text-muted">
          {strategy.members.length} yours
          {matchedCount > 0 && (
            <span className="text-faint">
              {' '}
              + {matchedCount} matched at {pct(threshold, 0)}+
            </span>
          )}
        </span>
      </div>

      {strategy.name_reading && (
        <p className="mt-1 text-[13px] text-faint">
          Your name reads like {strategy.name_reading} — shown for your benefit; matching uses the
          trades, not the name.
        </p>
      )}

      <div className="mt-4 flex gap-1 border-b border-line">
        {(
          [
            ['current', `Current${live.count > 0 ? ` · ${live.count} open` : ''}`],
            ['performance', 'Performance'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`-mb-px border-b-2 px-4 py-2 text-[15px] transition-colors ${
              tab === id
                ? 'border-accent font-medium text-accent'
                : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Current: only what is open, with every chart for it opened. */}
      {tab === 'current' && <LivePanel live={live} expanded />}

      {/* Performance: everything this strategy has done, open trades
          included, as it was before the tabs. */}
      {tab === 'performance' && (
      <div className="mt-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-[12px] uppercase tracking-wider text-muted">Past performance</span>
          <span className="text-[15px]">
            <span className="num">{stats.trades}</span> closed,{' '}
            <span className="num">{stats.winRate === null ? '—' : pct(stats.winRate, 0)}</span> won,{' '}
            <span className={`figure ${stats.total >= 0 ? 'text-profit' : 'text-loss'}`}>
              {money(stats.total, { sign: true, cents: false })}
            </span>{' '}
            banked, <span className="figure">{money(stats.expectancy, { sign: true, cents: false })}</span>{' '}
            <span className="text-muted">expected per trade</span>
          </span>

        </div>

        {live.count === 0 && running.count > 0 && (
          <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[14px]">
            <span className="text-[12px] uppercase tracking-wider text-accent">Still open</span>
            <span className="num font-medium">
              {running.count} {running.count === 1 ? 'trade' : 'trades'}
            </span>
            <span className="text-faint">worth</span>
            <span className={`figure font-medium ${running.pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
              {money(running.pnl, { sign: true, cents: false })}
            </span>
            <span className="text-faint">
              right now{running.partial ? ', and one of them is unpriced' : ''} — matched trades,
              not positions this strategy is named on.
            </span>
          </div>
        )}
      </div>
      )}

      {showPast && (
        <>
          <div className="mt-3 grid grid-cols-3 gap-3 border-t border-line pt-3 sm:grid-cols-7">
            <Stat label="Trades" value={String(stats.trades)} />
            <Stat label="Win rate" value={stats.winRate === null ? '—' : pct(stats.winRate, 0)} />
            <Stat
              label="Total P&L"
              value={money(stats.total, { sign: true, cents: false })}
              tone={stats.total >= 0 ? 'text-profit' : 'text-loss'}
            />
            <Stat
              label="Expectancy"
              value={money(stats.expectancy, { sign: true, cents: false })}
              tone={stats.expectancy >= 0 ? 'text-profit' : 'text-loss'}
            />
            <Stat label="Avg win" value={money(stats.avgWin, { cents: false })} />
            <Stat label="Avg loss" value={money(stats.avgLoss, { cents: false })} />
            <Stat label="Captured" value={stats.capture === null ? '—' : pct(stats.capture, 0)} />
          </div>

          <div className="mt-3 border-t border-line pt-2">
            <EquityCurve points={curve} />
          </div>

          <div className="mt-2 overflow-x-auto rounded-sm border border-line">
            <table className="w-full text-[15px]">
              <thead>
                <tr className="border-b-2 border-line-strong text-left text-[12px] font-semibold uppercase tracking-wider text-muted">
                  <th className="py-3 pl-4 pr-4">Status</th>
                  <th className="py-3 pr-4">Trade</th>
                  <th className="py-3 pr-4">When</th>
                  <th className="py-3 pr-4">Legs</th>
                  <th className="py-3 pr-4 text-right">Days to expiry</th>
                  <th className="py-3 pr-4 text-right">Took in / paid</th>
                  <th className="py-3 pr-4 text-right">P&L</th>
                  <th className="w-10 py-3 pr-3" />
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-4 text-[14px] text-muted">
                      Nothing in this strategy at {pct(threshold, 0)} confidence.
                    </td>
                  </tr>
                ) : (
                  (() => {
                    const shown = showAll ? rows : rows.slice(0, 15)
                    const openRows = shown.filter((r) => r.is_open)
                    const closedRows = shown.filter((r) => !r.is_open)
                    const heading = (label: string, n: number, tone: string) => (
                      <tr key={label}>
                        <td
                          colSpan={8}
                          className={`px-4 pb-2 pt-4 text-[13px] font-semibold uppercase tracking-wider ${tone}`}
                        >
                          {label} · {n}
                        </td>
                      </tr>
                    )
                    const row = (r: Row) => (
                      <HistoryRow
                        key={r.id}
                        r={r}
                        busy={busy === r.id}
                        onAdopt={() => void adopt(r.id)}
                        onDrop={() => void drop(r.id)}
                      />
                    )
                    return (
                      <>
                        {openRows.length > 0 && heading('Open now — not banked yet', openRows.length, 'text-accent')}
                        {openRows.map(row)}
                        {closedRows.length > 0 && heading('Closed — banked', closedRows.length, 'text-muted')}
                        {closedRows.map(row)}
                      </>
                    )
                  })()
                )}
              </tbody>
            </table>
          </div>

          {rows.length > 15 && (
            <button
              onClick={() => setShowAll(!showAll)}
              className="mt-1.5 text-[13px] text-muted hover:text-ink"
            >
              {showAll ? 'Show fewer' : `Show all ${rows.length}`}
            </button>
          )}

          {nearMisses.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-[13px] text-faint">
                {nearMisses.length} just below the bar — not counted
              </summary>
              <ul className="mt-1 space-y-0.5">
                {nearMisses.map((c) => (
                  <li key={c.trade_id} className="flex items-baseline gap-2 text-[13px]">
                    <span className="num w-9 shrink-0 text-right text-muted">
                      {pct(c.confidence, 0)}
                    </span>
                    <span className="w-20 shrink-0 text-faint">{shortDate(c.opened)}</span>
                    <span className="w-16 shrink-0 text-faint">{c.underlying}</span>
                    <span className="flex-1 truncate text-faint">
                      {[...c.misses, ...c.unknowns].join(' · ')}
                    </span>
                    <button
                      onClick={() => void adopt(c.trade_id)}
                      disabled={busy === c.trade_id}
                      className="shrink-0 rounded-sm px-1.5 text-[13px] text-muted hover:bg-hover hover:text-ink disabled:opacity-40"
                    >
                      add anyway
                    </button>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  )
}

/* The confidence bar, as a setting rather than a control on the page.

   It decides how the user's journal is read, so it belongs with his other
   preferences: set once, saved, and out of the way. What stays visible is one
   line saying what the bar is and how many trades it is letting in, which is
   the part he needs while reading the page.

   Open it, though, and the bar drives the page as it moves. It used to hold a
   draft that did nothing until Save was pressed, so the one thing it was for —
   drag it and watch what the app stops counting — could only be done one
   round-trip at a time. Every card re-filters on the live value now; Save only
   decides whether the position of the bar outlives the visit. */
function ThresholdSetting({
  value,
  saved,
  onPreview,
  onSave,
  added,
  strategies,
}: {
  value: number
  saved: number
  onPreview: (v: number | null) => void
  onSave: (v: number) => Promise<void>
  added: number
  strategies: number
}) {
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  async function save() {
    setSaving(true)
    setProblem(null)
    try {
      await onSave(value)
      onPreview(null)
      setOpen(false)
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-card border border-line bg-raised px-4 py-2">
      <div className="flex flex-wrap items-baseline gap-2 text-[13px]">
        <span className="text-muted">
          Counting trades the app is at least{' '}
          <span className="num font-medium text-ink">{pct(value, 0)}</span> sure about —{' '}
          <span className="num text-ink">{added}</span> matched across {strategies}{' '}
          {strategies === 1 ? 'strategy' : 'strategies'}.
        </span>
        <button
          onClick={() => {
            if (open) onPreview(null)
            setOpen(!open)
          }}
          className="ml-auto rounded-sm border border-line px-2 py-0.5 text-muted hover:bg-hover hover:text-ink"
        >
          {open ? 'Close' : 'Change'}
        </button>
      </div>

      {open && (
        <div className="mt-2 space-y-2 border-t border-line pt-2">
          <p className="text-[13px] text-muted">
            An old trade joins a strategy when the app is at least this sure it belongs. Lower it to
            see what it is refusing to count; the reasons are on every row.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <span className="num w-14 text-[21px] font-semibold">{pct(value, 0)}</span>
            <input
              type="range"
              min={50}
              max={100}
              step={1}
              value={Math.round(value * 100)}
              onChange={(e) => onPreview(Number(e.target.value) / 100)}
              className="h-1 min-w-[12rem] flex-1 cursor-pointer appearance-none rounded-full bg-sunken accent-accent"
              aria-label="Minimum confidence for a trade to count"
            />
            <button
              onClick={() => onPreview(null)}
              disabled={value === saved}
              className="rounded-sm border border-line px-3 py-1 text-[13px] text-muted hover:bg-hover hover:text-ink disabled:opacity-40"
            >
              Back to {pct(saved, 0)}
            </button>
            <button
              onClick={() => void save()}
              disabled={saving || value === saved}
              className="rounded-sm border border-accent/50 bg-accent-soft px-3 py-1 text-[13px] text-accent disabled:opacity-40"
            >
              {saving ? 'Saving…' : 'Keep it'}
            </button>
          </div>
          {problem && <div className="text-[13px] text-loss">{problem}</div>}
        </div>
      )}
    </div>
  )
}

export function Strategies() {
  const named = useAsync(() => api.namedStrategies(), [])
  const matches = useAsync(() => api.allMatches(), [])
  const settings = useAsync(() => api.settings(), [])
  const openViews = useAsync(() => api.openStrategies(), [])
  const fields = useAsync(() => api.fields(), [])
  const detail = useMemo(
    () => ({
      views: new Map((openViews.data ?? []).map((v) => [v.strategy.id, v])),
      legColumns: pickFields(fields.data?.leg, settings.data?.leg_columns),
      legCatalogue: fields.data?.leg,
      onLegColumns: async (ids: string[]) => {
        await api.setSetting('leg_columns', JSON.stringify(ids))
        settings.reload()
      },
    }),
    [openViews.data, fields.data, settings],
  )

  const saved = settings.data?.match_threshold ?? 0.97
  // What the page is being read at right now. Null means "whatever is saved";
  // a number is the bar being dragged, and every card below follows it.
  const [preview, setPreview] = useState<number | null>(null)
  // Narrows the cards to the strategies that mention every word typed.
  const [query, setQuery] = useState('')
  const threshold = preview ?? saved
  const data = named.data
  const reports = matches.data

  const added = useMemo(() => {
    if (!reports) return 0
    return Object.values(reports).reduce(
      (acc, r) => acc + r.candidates.filter((c) => c.confidence >= threshold).length,
      0,
    )
  }, [reports, threshold])

  if (named.error) return <ErrorPanel error={named.error} onRetry={named.reload} />
  if (!data) return <Loading label="Reading your strategies" />

  // What is live comes first, worst first inside that. A strategy with nothing
  // on is a record; a strategy with something on is a decision waiting.
  const ordered = [...data].sort((a, b) => {
    if ((a.live.count > 0) !== (b.live.count > 0)) return a.live.count > 0 ? -1 : 1
    const rank = (s: NamedStrategy) =>
      s.live.worst_risk ? DANGER_ORDER.indexOf(s.live.worst_risk) : -1
    if (rank(a) !== rank(b)) return rank(b) - rank(a)
    return (a.live.dte ?? 9999) - (b.live.dte ?? 9999)
  })

  function reload() {
    named.reload()
    matches.reload()
  }

  async function saveThreshold(value: number) {
    await api.setSetting('match_threshold', value)
    settings.reload()
  }

  return (
    <DetailContext.Provider value={detail}>
    <div className="space-y-3">
      <SectionHeading
        title="Your strategies"
        hint="what you grouped, plus every older trade the app is sure enough about"
      />

      {data.length === 0 ? (
        <Empty
          title="You have not named any strategies yet."
          hint="Go to Legs, pick the legs that belong to one idea, and name it. The app will then look back through your history for trades like it and say how sure it is about each one."
        />
      ) : (
        <>
          <ThresholdSetting
            value={threshold}
            saved={saved}
            onPreview={setPreview}
            onSave={saveThreshold}
            added={added}
            strategies={data.length}
          />
          {matches.loading && !reports && (
            <p className="text-[13px] text-faint">Looking back through your history…</p>
          )}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search — a name, a product, a structure, a note…"
            className="w-full rounded-card border border-line bg-raised px-4 py-2.5 text-[16px] outline-none focus:border-accent/60"
          />
          {(() => {
            const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
            const found = ordered.filter((s) => {
              if (words.length === 0) return true
              const hay = [
                s.name,
                s.product,
                s.shape,
                s.note ?? '',
                ...s.members.map((m) => `${m.underlying} ${m.structure}`),
                ...s.live.positions.map((p) => `${p.underlying} ${p.structure}`),
              ]
                .join(' ')
                .toLowerCase()
              return words.every((w) => hay.includes(w))
            })
            return found.length === 0 ? (
              <p className="py-4 text-[15px] text-muted">No strategy matches “{query}”.</p>
            ) : (
              found.map((s) => (
                <StrategyCard
                  key={s.id}
                  strategy={s}
                  report={reports?.[s.id]}
                  threshold={threshold}
                  onChange={reload}
                />
              ))
            )
          })()}
        </>
      )}
    </div>
    </DetailContext.Provider>
  )
}
