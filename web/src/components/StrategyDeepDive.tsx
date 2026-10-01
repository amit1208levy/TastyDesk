import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { EquityCurve } from './EquityCurve'
import { Empty, ErrorPanel, Loading, SectionHeading } from './States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { useWidth } from '../lib/useMeasure'
import { money, num, pct, decimals, shortDate, fullDate, EM_DASH } from '../lib/format'
import type { Period, ReportTrade, StrategyReport } from '../types'

/* One strategy at a time, told so it can be understood.

   The table above it ranks groups in nine columns of numbers. This answers the
   question behind them: how does this strategy make its money, and how does it
   lose it? A plain-English summary leads — the backend writes it from the same
   figures — and the pictures under it each answer one thing: the running total
   (is it growing?), the months (is it steady?), every trade's result (is it
   many small wins and a few big losses?), and the trades that decided it.

   Charts are drawn by hand like the equity curve beside them: thin marks,
   bars that grow from one zero line with their rounded end at the data, a
   2px gap between neighbours, and a caption under each chart that answers
   whatever the cursor is over. Winning and losing are told apart by which side
   of zero a bar sits on before its colour says the same. */

type Grouping = 'named' | 'structure' | 'product'

const GROUPINGS: { id: Grouping; label: string }[] = [
  { id: 'named', label: 'My strategies' },
  { id: 'structure', label: 'Structure' },
  { id: 'product', label: 'Product' },
]

const card = 'surface sheened'

function signed(v: string | number | null | undefined) {
  return money(v, { sign: true, cents: false })
}

function tone(v: string | number | null | undefined) {
  const n = num(v)
  if (n === null || n === 0) return 'text-muted'
  return n > 0 ? 'text-profit' : 'text-loss'
}

/* ---- tiny running-total line for the list ------------------------------------ */

function Sparkline({ report }: { report: StrategyReport }) {
  const values = report.curve.map((p) => num(p.cumulative) ?? 0)
  if (values.length < 2) return <div className="h-7" />
  const w = 96
  const h = 28
  const hi = Math.max(0, ...values)
  const lo = Math.min(0, ...values)
  const span = hi - lo || 1
  const x = (i: number) => (i / (values.length - 1)) * w
  const y = (v: number) => h - 2 - ((v - lo) / span) * (h - 4)
  const d = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const up = values[values.length - 1] >= 0
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-7 w-24 shrink-0" aria-hidden>
      <line x1={0} x2={w} y1={y(0)} y2={y(0)} className="stroke-line-strong" strokeWidth={1} />
      <path d={d} fill="none" strokeWidth={1.5} strokeLinejoin="round" className={up ? 'stroke-profit' : 'stroke-loss'} />
    </svg>
  )
}

/* ---- months --------------------------------------------------------------------- */

const MH = 170
const MP = { top: 10, right: 8, bottom: 24, left: 56 }

function monthLabel(m: string, withYear: boolean) {
  const d = new Date(`${m}-01T12:00:00`)
  const name = d.toLocaleDateString('en-US', { month: 'short' })
  return withYear ? `${name} '${String(d.getFullYear()).slice(2)}` : name
}

/* A bar with its rounded end at the value and its square end on the zero line. */
function barPath(x: number, w: number, y0: number, y1: number) {
  const up = y1 < y0
  const h = Math.abs(y0 - y1)
  const r = Math.min(4, w / 2, h)
  if (h < 0.5) return `M${x},${y0} h${w}`
  return up
    ? `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 + r} V${y0} Z`
    : `M${x},${y0} V${y1 - r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 - r} V${y0} Z`
}

function MonthBars({ months }: { months: StrategyReport['months'] }) {
  const [hover, setHover] = useState<number | null>(null)
  // Drawn at the width it is shown at, so the labels stay 11px.
  const [box, measured] = useWidth<HTMLDivElement>()
  const MW = measured ?? 640
  if (months.length === 0) return <Empty title="No closed months." />
  const values = months.map((m) => num(m.pnl) ?? 0)
  const hi = Math.max(0, ...values)
  const lo = Math.min(0, ...values)
  const span = hi - lo || 1
  const innerW = MW - MP.left - MP.right
  const innerH = MH - MP.top - MP.bottom
  const slot = innerW / months.length
  const bw = Math.min(24, Math.max(slot - 2, 2) * 0.7)
  const y = (v: number) => MP.top + innerH - ((v - lo) / span) * innerH
  const every = Math.max(1, Math.ceil(months.length / 8))
  const shown = hover === null ? null : months[hover]
  const positive = values.filter((v) => v > 0).length

  return (
    <div ref={box}>
      <svg
        viewBox={`0 0 ${MW} ${MH}`}
        width={MW}
        height={MH}
        className="block max-w-full"
        role="img"
        aria-label={`Profit and loss by month: ${positive} of ${months.length} months positive`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect()
          const rel = ((e.clientX - box.left) / box.width) * MW - MP.left
          const i = Math.floor(rel / slot)
          setHover(i >= 0 && i < months.length ? i : null)
        }}
      >
        {[hi, lo].filter((v, i, a) => a.indexOf(v) === i && v !== 0).map((v) => (
          <g key={v}>
            <line x1={MP.left} x2={MW - MP.right} y1={y(v)} y2={y(v)} className="stroke-chart-grid" strokeWidth={1} />
            <text x={MP.left - 8} y={y(v) + 4} textAnchor="end" className="fill-faint text-[11px]">
              {signed(v)}
            </text>
          </g>
        ))}
        <line x1={MP.left} x2={MW - MP.right} y1={y(0)} y2={y(0)} className="stroke-line-strong" strokeWidth={1} />
        <text x={MP.left - 8} y={y(0) + 4} textAnchor="end" className="fill-faint text-[11px]">
          $0
        </text>
        {months.map((m, i) => {
          const v = values[i]
          const x = MP.left + i * slot + (slot - bw) / 2
          return (
            <g key={m.month}>
              <path
                d={barPath(x, bw, y(0), y(v))}
                className={v >= 0 ? 'fill-chart-profit' : 'fill-chart-loss'}
                opacity={hover === null || hover === i ? 1 : 0.45}
              />
              {i % every === 0 && (
                <text x={x + bw / 2} y={MH - 6} textAnchor="middle" className="fill-faint text-[11px]">
                  {monthLabel(m.month, i === 0 || m.month.endsWith('-01'))}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <div className="mt-1 h-5 text-[13px] text-muted">
        {shown ? (
          <span className="num">
            {monthLabel(shown.month, true)} · <span className="text-ink">{signed(shown.pnl)}</span> · {shown.trades}{' '}
            trade{shown.trades === 1 ? '' : 's'}, {shown.wins} won
          </span>
        ) : (
          <span className="text-faint">
            {positive} of {months.length} months ended positive
          </span>
        )}
      </div>
    </div>
  )
}

/* ---- every trade's result ---------------------------------------------------------- */

const DH = 170
const DP = { top: 12, right: 8, bottom: 26, left: 8 }

function binText(b: StrategyReport['distribution'][number]) {
  const n = `${b.trades} trade${b.trades === 1 ? '' : 's'}`
  if (b.open_low) return `${n} at ${signed(b.high)} or worse`
  if (b.open_high) return `${n} at ${signed(b.low)} or better`
  return `${n} between ${signed(b.low)} and ${signed(b.high)}`
}

function Outcomes({ bins }: { bins: StrategyReport['distribution'] }) {
  const [hover, setHover] = useState<number | null>(null)
  const [box, measured] = useWidth<HTMLDivElement>()
  const DW = measured ?? 640
  if (bins.length === 0) return null
  const top = Math.max(...bins.map((b) => b.trades), 1)
  const innerW = DW - DP.left - DP.right
  const innerH = DH - DP.top - DP.bottom
  const slot = innerW / bins.length
  const y = (n: number) => DP.top + innerH - (n / top) * innerH
  const zeroAt = bins.findIndex((b) => (num(b.low) ?? 0) >= 0)
  const shown = hover === null ? null : bins[hover]
  const losers = bins.filter((b) => (num(b.high) ?? 0) <= 0).reduce((a, b) => a + b.trades, 0)
  const winners = bins.reduce((a, b) => a + b.trades, 0) - losers

  const edge = (i: number) => DP.left + i * slot
  const labelEvery = Math.max(1, Math.ceil(bins.length / 7))

  return (
    <div ref={box}>
      <svg
        viewBox={`0 0 ${DW} ${DH}`}
        width={DW}
        height={DH}
        className="block max-w-full"
        role="img"
        aria-label={`Each trade's result, bucketed: ${losers} losing trades left of zero, ${winners} winning trades right of it`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect()
          const rel = ((e.clientX - box.left) / box.width) * DW - DP.left
          const i = Math.floor(rel / slot)
          setHover(i >= 0 && i < bins.length ? i : null)
        }}
      >
        <line x1={DP.left} x2={DW - DP.right} y1={y(0)} y2={y(0)} className="stroke-line-strong" strokeWidth={1} />
        {bins.map((b, i) => {
          const loss = (num(b.high) ?? 0) <= 0
          const x = edge(i) + 1
          const w = Math.max(slot - 2, 1)
          return (
            <g key={i}>
              {b.trades > 0 && (
                <path
                  d={barPath(x, w, y(0), y(b.trades))}
                  className={loss ? 'fill-chart-loss' : 'fill-chart-profit'}
                  opacity={hover === null || hover === i ? 1 : 0.45}
                />
              )}
              {b.trades > 0 && (
                <text x={x + w / 2} y={y(b.trades) - 4} textAnchor="middle" className="fill-muted text-[11px]">
                  {b.trades}
                </text>
              )}
            </g>
          )
        })}
        {zeroAt > 0 && (
          <line x1={edge(zeroAt)} x2={edge(zeroAt)} y1={DP.top} y2={y(0) + 4} className="stroke-ink" strokeWidth={1} opacity={0.5} />
        )}
        {bins.map((b, i) =>
          i % labelEvery === 0 || i === zeroAt ? (
            <text key={`l${i}`} x={edge(i)} y={DH - 8} textAnchor={i === 0 ? 'start' : 'middle'} className="fill-faint text-[11px]">
              {i === 0 && b.open_low ? `≤ ${signed(b.high)}` : signed(b.low)}
            </text>
          ) : null,
        )}
        <text x={DW - DP.right} y={DH - 8} textAnchor="end" className="fill-faint text-[11px]">
          {bins[bins.length - 1].open_high ? `≥ ${signed(bins[bins.length - 1].low)}` : signed(bins[bins.length - 1].high)}
        </text>
      </svg>
      <div className="mt-1 h-5 text-[13px] text-muted">
        {shown ? (
          <span className="num">
            {binText(shown)} · total <span className="text-ink">{signed(shown.pnl)}</span>
          </span>
        ) : (
          <span className="text-faint">
            {losers} losers on the left of $0, {winners} winners on the right
          </span>
        )}
      </div>
    </div>
  )
}

/* ---- wins against losses ---------------------------------------------------------- */

function Balance({ report }: { report: StrategyReport }) {
  const s = report.stats
  const win = num(s.avg_win) ?? 0
  const loss = num(s.avg_loss) ?? 0
  const top = Math.max(win, loss, 1)
  const ratio = win > 0 ? loss / win : null
  const row = (label: string, value: number, cls: string, n: number) => (
    <div className="grid grid-cols-[110px_1fr_auto] items-center gap-3">
      <span className="text-[14px] text-muted">{label}</span>
      <div className="h-3 overflow-hidden rounded-full bg-sunken">
        <div className={`h-full rounded-full ${cls}`} style={{ width: `${(value / top) * 100}%` }} />
      </div>
      <span className="num text-[15px] font-semibold">
        {money(value, { cents: false })} <span className="text-[12px] font-normal text-faint">× {n}</span>
      </span>
    </div>
  )
  return (
    <div className={`${card} flex h-full flex-col justify-center px-6 py-5`}>
      <div className="label">Wins against losses</div>
      <div className="mt-4 space-y-3">
        {row('Average win', win, 'bg-chart-profit', s.wins)}
        {row('Average loss', loss, 'bg-chart-loss', s.losses)}
      </div>
      <p className="mt-4 text-[14px] text-muted">
        {ratio === null || s.losses === 0
          ? 'No losses yet to weigh the wins against.'
          : ratio >= 1
            ? `One average loss is ${ratio.toFixed(1)} average wins. At a ${pct(s.win_rate, 0)} win rate it needs about ${pct(ratio / (1 + ratio), 0)} winners to break even.`
            : `An average loss is smaller than an average win, so it breaks even at about a ${pct(ratio / (1 + ratio), 0)} win rate.`}
      </p>
    </div>
  )
}

/* ---- small pieces ------------------------------------------------------------------ */

function Tile({ label, value, sub, valueClass = 'text-ink' }: { label: string; value: ReactNode; sub?: ReactNode; valueClass?: string }) {
  return (
    <div className={`${card} px-5 py-4`}>
      <div className="label">{label}</div>
      <div className={`figure mt-1.5 text-[26px] leading-none ${valueClass}`}>{value}</div>
      {sub && <div className="mt-1.5 text-[13px] text-muted">{sub}</div>}
    </div>
  )
}

function TradeLines({ title, trades, empty }: { title: string; trades: ReportTrade[]; empty: string }) {
  return (
    <div className={`${card} px-5 py-4`}>
      <div className="label">{title}</div>
      {trades.length === 0 ? (
        <div className="mt-3 text-[14px] text-faint">{empty}</div>
      ) : (
        <ul className="mt-2 divide-y divide-line">
          {trades.map((t) => (
            <li key={t.id} className="flex items-baseline justify-between gap-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-[15px]">
                  {t.underlying} <span className="text-muted">{t.structure}</span>
                </div>
                <div className="text-[12px] text-faint">
                  {shortDate(t.opened)} → {shortDate(t.closed)} · {t.days}d{t.rolls ? ` · rolled ${t.rolls}×` : ''}
                </div>
              </div>
              <span className={`num shrink-0 text-[15px] font-semibold ${tone(t.pnl)}`}>{signed(t.pnl)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Breakdown({ report }: { report: StrategyReport }) {
  const rows = report.breakdown
  const top = Math.max(...rows.map((r) => Math.abs(num(r.pnl) ?? 0)), 1)
  return (
    <div className={`${card} px-5 py-4`}>
      <div className="label">{report.breakdown_label}</div>
      <ul className="mt-2 space-y-2.5">
        {rows.slice(0, 8).map((r) => {
          const v = num(r.pnl) ?? 0
          return (
            <li key={r.key}>
              <div className="flex items-baseline justify-between gap-2 text-[14px]">
                <span className="truncate">{r.key}</span>
                <span className={`num shrink-0 ${tone(r.pnl)}`}>{signed(r.pnl)}</span>
              </div>
              {/* Diverging from the middle: losses grow left, profits right. */}
              <div className="relative mt-1 h-1.5 rounded-full bg-sunken">
                <div className="absolute inset-y-0 left-1/2 w-px bg-line-strong" />
                <div
                  className={`absolute inset-y-0 rounded-full ${v >= 0 ? 'bg-chart-profit' : 'bg-chart-loss'}`}
                  style={v >= 0 ? { left: '50%', width: `${(v / top) * 50}%` } : { right: '50%', width: `${(-v / top) * 50}%` }}
                />
              </div>
              <div className="mt-0.5 text-[12px] text-faint">
                {r.trades} trades · {pct(r.trades ? r.wins / r.trades : null, 0)} won
              </div>
            </li>
          )
        })}
      </ul>
      {rows.length > 8 && <div className="mt-2 text-[12px] text-faint">and {rows.length - 8} more</div>}
    </div>
  )
}

function Lens({ report }: { report: StrategyReport }) {
  const l = report.lens
  const items = [
    {
      label: 'Losses past Tom\'s stop',
      value: String(l.past_stop),
      sub: l.past_stop ? `${money(l.beyond_stop, { cents: false })} lost beyond the stop` : 'every loss stopped in time',
      bad: l.past_stop > 0,
    },
    {
      label: 'Rolled',
      value: String(l.rolled),
      sub: l.rolled ? `${signed(l.rolled_pnl)} on those trades` : 'never rolled',
      bad: l.rolled > 0 && (num(l.rolled_pnl) ?? 0) < 0,
    },
    {
      label: 'Winners held past target',
      value: l.winners_judged ? `${l.held_past_target} of ${l.winners_judged}` : EM_DASH,
      sub: 'Tom takes 50% (90% on an 11x)',
      bad: false,
    },
    {
      label: 'Lost over 2% of the account',
      value: String(l.over_two_pct),
      sub: l.over_two_pct ? `${signed(l.over_two_pct_pnl)} on those trades` : "inside Tom's 2% cap",
      bad: l.over_two_pct > 0,
    },
  ]
  return (
    <div className={`${card} px-6 py-5`}>
      <div className="label">Through Tom's rules</div>
      <div className="mt-3 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {items.map((i) => (
          <div key={i.label}>
            <div className="text-[13px] text-muted">{i.label}</div>
            <div className={`figure mt-1 text-[24px] leading-none ${i.bad ? 'text-loss' : 'text-ink'}`}>{i.value}</div>
            <div className="mt-1 text-[12px] text-faint">{i.sub}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function AllTrades({ trades }: { trades: ReportTrade[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={card}>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-6 py-4 text-left hover:bg-hover"
      >
        <span className="label">Every trade ({trades.length})</span>
        <span className="text-[13px] text-accent">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && (
        <div className="max-h-[480px] overflow-auto border-t border-line">
          <table className="w-full text-[14px]">
            <thead className="sticky top-0 bg-raised-solid">
              <tr className="text-left text-[12px] uppercase tracking-wider text-faint">
                <th className="py-2.5 pl-6 pr-3 font-medium">Closed</th>
                <th className="w-full py-2.5 pr-3 font-medium">Trade</th>
                <th className="py-2.5 pr-3 text-right font-medium">DTE in</th>
                <th className="py-2.5 pr-3 text-right font-medium">Days</th>
                <th className="py-2.5 pr-6 text-right font-medium">P&amp;L</th>
              </tr>
            </thead>
            <tbody className="num">
              {trades.map((t) => (
                <tr key={t.id} className="border-t border-line/60 hover:bg-hover">
                  <td className="whitespace-nowrap py-2 pl-6 pr-3 text-muted">{fullDate(t.closed)}</td>
                  <td className="py-2 pr-3">
                    {t.underlying} <span className="text-muted">{t.structure}</span>
                    {t.rolls > 0 && <span className="text-faint"> · rolled {t.rolls}×</span>}
                  </td>
                  <td className="py-2 pr-3 text-right text-muted">{t.dte_at_entry ?? EM_DASH}</td>
                  <td className="py-2 pr-3 text-right text-muted">{t.days}</td>
                  <td className={`whitespace-nowrap py-2 pr-6 text-right font-medium ${tone(t.pnl)}`}>{signed(t.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/* ---- the detail -------------------------------------------------------------------- */

const VERDICT: Record<StrategyReport['verdict'], string> = {
  'making money': 'bg-profit-soft text-ok',
  'losing money': 'bg-danger-soft text-danger',
  'about even': 'bg-sunken text-muted',
  'no closed trades': 'bg-sunken text-faint',
}

function Detail({ report }: { report: StrategyReport }) {
  const s = report.stats
  const points = useMemo(
    () =>
      report.curve.map((p) => ({
        date: p.date,
        pnl: num(p.pnl) ?? 0,
        cumulative: num(p.cumulative) ?? 0,
        id: p.id,
      })),
    [report],
  )
  const months = num(report.months_active)
  const showProduct = report.product && !report.name.toUpperCase().includes(report.product.toUpperCase())

  return (
    <div key={report.key} className="fade-in space-y-4">
      <div className={`${card} relative overflow-hidden px-7 py-6`}>
        <div className="flex flex-wrap items-center gap-2.5">
          <h3 className="display text-[30px]">{report.name}</h3>
          {showProduct && <span className="rounded-full bg-sunken px-2.5 py-0.5 text-[13px] text-muted">{report.product}</span>}
          <span className={`rounded-full px-2.5 py-0.5 text-[12px] font-semibold uppercase tracking-[0.06em] ${VERDICT[report.verdict]}`}>
            {report.verdict}
          </span>
          {report.thin && (
            <span className="rounded-full bg-sunken px-2.5 py-0.5 text-[12px] text-faint" title="Fewer than 20 closed trades: read the numbers as a hint, not a verdict">
              thin sample
            </span>
          )}
        </div>
        <div className="mt-4 flex flex-wrap items-baseline gap-x-5 gap-y-1">
          <span className={`figure text-[44px] leading-none ${tone(s.total_pnl)}`}>{signed(s.total_pnl)}</span>
          <span className="text-[15px] text-muted">
            {s.trades} trades{months ? ` over ${months.toFixed(0)} month${months >= 1.5 ? 's' : ''}` : ''} ·{' '}
            {signed(s.expectancy)} a trade
            {report.per_month !== null && s.trades >= 3 ? ` · ${signed(report.per_month)} a month` : ''}
          </span>
        </div>
        <ul className="mt-5 space-y-2.5">
          {report.summary.map((line, i) => (
            <li key={i} className="flex gap-3 text-[16px] leading-relaxed">
              <span className={`mt-[11px] h-1.5 w-1.5 shrink-0 rounded-full ${i === 0 ? 'bg-accent' : 'bg-line-strong'}`} />
              <span className={i === 0 ? 'text-ink' : 'text-muted'}>{line}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-4">
        <Tile label="Win rate" value={pct(s.win_rate, 0)} sub={`${s.wins} won · ${s.losses} lost`} />
        <Tile
          label={`Last ${report.recent_trades} trades`}
          value={signed(report.recent_pnl)}
          valueClass={tone(report.recent_pnl)}
          sub={`${report.recent_wins} of ${report.recent_trades} won`}
        />
        <Tile label="Profit factor" value={s.profit_factor === null ? EM_DASH : decimals(s.profit_factor, 2)} sub="dollars won per dollar lost" />
        <Tile label="Days held" value={decimals(s.avg_days_in_trade, 0)} sub="on average" />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Balance report={report} />
        <div className={`${card} px-6 py-5`}>
          <div className="label">Running total</div>
          <div className="mt-3">
            <EquityCurve points={points} />
          </div>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className={`${card} px-6 py-5`}>
          <div className="label">Month by month</div>
          <div className="mt-3">
            <MonthBars months={report.months} />
          </div>
        </div>
        <div className={`${card} px-6 py-5`}>
          <div className="flex items-baseline justify-between gap-3">
            <div className="label">Every trade's result</div>
            <span className="text-[12px] text-faint">how many trades ended in each range</span>
          </div>
          <div className="mt-3">
            <Outcomes bins={report.distribution} />
          </div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <TradeLines title="Best trades" trades={report.best} empty="No winners yet." />
        <TradeLines title="Worst trades" trades={report.worst} empty="No losers yet." />
        <Breakdown report={report} />
      </div>

      <Lens report={report} />
      <AllTrades trades={report.trades} />
    </div>
  )
}

/* ---- the section -------------------------------------------------------------------- */

export function StrategyDeepDive({ period }: { period: Period }) {
  const [grouping, setGrouping] = useState<Grouping>(() => {
    try {
      return (localStorage.getItem('td-deep-dive') as Grouping) || 'named'
    } catch {
      return 'named'
    }
  })
  const [selected, setSelected] = useState<string | null>(null)
  const { data, error, loading, reload } = useAsync(() => api.strategyReports(grouping, period), [grouping, period])

  useEffect(() => {
    try {
      localStorage.setItem('td-deep-dive', grouping)
    } catch {
      /* the choice just will not be remembered */
    }
  }, [grouping])

  // The pick while the list still has it; otherwise the biggest strategy.
  const current = data?.find((r) => r.key === selected) ?? data?.[0] ?? null

  return (
    <section>
      <SectionHeading title="Each strategy, up close" hint="how it makes its money, and how it loses it" />
      {/* Its own row rather than the heading's right edge: on a phone the
          heading and three buttons do not fit on one line. */}
      <div className="-mt-1 mb-4 flex">
        <div className="flex rounded-full bg-sunken p-0.5" role="tablist" aria-label="Group trades by">
          {GROUPINGS.map((g) => (
            <button
              key={g.id}
              role="tab"
              aria-selected={grouping === g.id}
              onClick={() => {
                setGrouping(g.id)
                setSelected(null)
              }}
              className={`rounded-full px-3 py-1 text-[13px] ${
                grouping === g.id ? 'bg-raised-solid font-medium text-ink shadow-[var(--shadow-sm)]' : 'text-muted hover:text-ink'
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
      </div>
      {error ? (
        <ErrorPanel error={error} onRetry={reload} />
      ) : !data ? (
        <Loading label="Reading each strategy's record" />
      ) : data.length === 0 ? (
        <Empty title="No closed trades in this period." />
      ) : (
        <div className={`grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)] ${loading ? 'opacity-70' : ''}`}>
          <nav
            aria-label="Strategies"
            className="flex gap-2 overflow-x-auto pb-1 xl:sticky xl:top-28 xl:block xl:max-h-[calc(100vh-8rem)] xl:space-y-1.5 xl:self-start xl:overflow-y-auto xl:overflow-x-visible xl:pb-0"
          >
            {data.map((r) => {
              const active = r.key === current?.key
              return (
                <button
                  key={r.key}
                  onClick={() => setSelected(r.key)}
                  aria-current={active ? 'true' : undefined}
                  className={`flex w-full min-w-[240px] items-center gap-3 rounded-sm border px-3.5 py-2.5 text-left xl:min-w-0 ${
                    active
                      ? 'border-accent/50 bg-accent-soft'
                      : 'border-line bg-raised hover:bg-hover'
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <div className={`truncate text-[15px] ${active ? 'font-semibold text-ink' : 'text-ink'}`}>{r.name}</div>
                    <div className="truncate text-[12px] text-faint">
                      {r.product && r.product !== r.name ? `${r.product} · ` : ''}
                      {r.stats.trades} trades · {pct(r.stats.win_rate, 0)} won
                    </div>
                  </div>
                  <div className="flex flex-col items-end">
                    <span className={`num text-[14px] font-semibold ${tone(r.stats.total_pnl)}`}>{signed(r.stats.total_pnl)}</span>
                    <Sparkline report={r} />
                  </div>
                </button>
              )
            })}
          </nav>
          {current ? <Detail report={current} /> : <div />}
        </div>
      )}
    </section>
  )
}
