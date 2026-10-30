import { useMemo, useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, num, pct, shortDate } from '../lib/format'
import { StatTile } from './StatTile'
import { SectionHeading } from './States'

const W = 640
const H = 150
const PAD = { top: 10, right: 10, bottom: 18, left: 44 }

/* The account itself, not the trades: net liq at each close with deposits taken
   out, as a time-weighted growth line, and SPY over the same days in grey. The
   one question the trade tables cannot answer is whether all of it, together,
   beat simply holding the market. */
export function AccountRecord() {
  const curve = useAsync(() => api.accountCurve(), [])
  const [hover, setHover] = useState<number | null>(null)
  const c = curve.data ?? null

  const model = useMemo(() => {
    if (!c || c.days.length < 2) return null
    const mine = c.days.map((d) => num(d.growth) ?? 0)
    const spy = c.days.map((d) => num(d.spy))
    const all = [0, ...mine, ...spy.filter((v): v is number => v !== null)]
    const hi = Math.max(...all)
    const lo = Math.min(...all)
    const span = hi - lo || 1
    const innerW = W - PAD.left - PAD.right
    const innerH = H - PAD.top - PAD.bottom
    const x = (i: number) => PAD.left + (i / (c.days.length - 1)) * innerW
    const y = (v: number) => PAD.top + innerH - ((v - lo) / span) * innerH
    const line = (vals: (number | null)[]) =>
      vals
        .map((v, i) => (v === null ? null : `${x(i)},${y(v)}`))
        .filter((p): p is string => p !== null)
        .map((p, k) => `${k === 0 ? 'M' : 'L'}${p}`)
        .join(' ')
    return { x, y, hi, lo, innerW, mineLine: line(mine), spyLine: line(spy) }
  }, [c])

  if (curve.error || !c || !model) return null

  const growth = num(c.growth)
  const spy = num(c.spy)
  const gain = num(c.gain) ?? 0
  const beat = growth !== null && spy !== null ? growth - spy : null
  const shown = hover === null ? null : c.days[hover]
  const first = c.days[0].day

  return (
    <section>
      <SectionHeading
        title="The account"
        hint={`since ${shortDate(first)} · deposits taken out`}
        help="Net liq at each day's close, with money you wired in or out taken out, so a deposit is never counted as a gain. The percentage is time-weighted: each day's move over the day before, chained. The grey line is SPY over the same days."
      />
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <StatTile
          label="Made trading"
          value={money(gain, { sign: true, cents: false })}
          tone={gain >= 0 ? 'profit' : 'loss'}
          sub={`net liq change less ${money(c.deposits, { cents: false })} deposited`}
        />
        <StatTile
          label="Growth"
          value={growth === null ? '—' : pct(growth, 1, true)}
          tone={growth === null ? 'muted' : growth >= 0 ? 'profit' : 'loss'}
          sub="time-weighted"
        />
        <StatTile
          label="Against SPY"
          value={beat === null ? '—' : pct(beat, 1, true)}
          tone={beat === null ? 'muted' : beat >= 0 ? 'profit' : 'loss'}
          sub={spy === null ? 'no SPY prices' : `SPY ${pct(spy, 1, true)} over the same days`}
        />
        <StatTile
          label="Worst fall"
          value={money(-Math.abs(num(c.drawdown) ?? 0), { cents: false })}
          tone="loss"
          sub={
            c.drawdown_from && c.drawdown_to
              ? `${shortDate(c.drawdown_from)} → ${shortDate(c.drawdown_to)}`
              : 'from a high'
          }
        />
      </div>
      <div className="surface sheened mt-3 px-4 py-3">
        <div className="flex items-baseline justify-between gap-3 text-[13px]">
          <span className="text-muted">
            <span className="mr-1.5 inline-block h-0.5 w-4 bg-accent align-middle" />
            You
            <span className="ml-4 mr-1.5 inline-block h-0.5 w-4 bg-line-strong align-middle" />
            SPY
          </span>
          <span className="num text-faint">
            {shown
              ? `${shortDate(shown.day)} · you ${pct(num(shown.growth), 1, true)} · SPY ${
                  shown.spy === null ? '—' : pct(num(shown.spy), 1, true)
                } · net liq ${money(shown.net_liq, { cents: false })}`
              : c.skipped.length > 0
                ? `${c.skipped.length} day${c.skipped.length === 1 ? '' : 's'} left out: the broker's net liq jumped and came straight back`
                : ''}
          </span>
        </div>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="mt-1 w-full"
          role="img"
          aria-label={`Account growth ${pct(growth, 1, true)} against SPY ${pct(spy, 1, true)}`}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const box = e.currentTarget.getBoundingClientRect()
            const rel = ((e.clientX - box.left) / box.width) * W
            const i = Math.round(((rel - PAD.left) / model.innerW) * (c.days.length - 1))
            setHover(Math.max(0, Math.min(c.days.length - 1, i)))
          }}
        >
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={model.y(0)}
            y2={model.y(0)}
            className="stroke-line-strong"
            strokeDasharray="3 3"
          />
          {[model.hi, model.lo].map((v) => (
            <text key={v} x={PAD.left - 6} y={model.y(v) + 4} textAnchor="end" className="fill-faint text-[10px]">
              {pct(v, 0, true)}
            </text>
          ))}
          <path d={model.spyLine} fill="none" strokeWidth={1.5} className="stroke-line-strong" />
          <path d={model.mineLine} fill="none" strokeWidth={2} strokeLinejoin="round" className="stroke-accent" />
          {hover !== null && (
            <line
              x1={model.x(hover)}
              x2={model.x(hover)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              className="stroke-line-strong"
            />
          )}
        </svg>
      </div>
    </section>
  )
}
