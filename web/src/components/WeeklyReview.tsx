import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { useNarrow } from '../lib/useMeasure'
import { money, num, pct, shortDate } from '../lib/format'
import { StatTile } from './StatTile'
import { Empty, ErrorPanel, Loading, SectionHeading } from './States'
import type { WeekBucket, WeekClosed, WeekDimension, WeekOpened, WeekReview, WeekRow, WeekVerdict } from '../types'

/* The week in review. On a weekend the market is shut and "down $3 today" says
   nothing; what helps is the week: what the account did against SPY, what was
   closed and opened, how the book moved, and above all whether the week moved
   it toward the plan (greeks, allocation, profit) or away from it. Every
   figure comes from /api/week; a reading that was not recorded says so. */

const card = 'surface sheened'

const VERDICT_TONE: Record<WeekVerdict['tone'], string> = {
  good: 'bg-profit-soft text-profit',
  bad: 'bg-danger-soft text-danger',
  mixed: 'bg-tested-soft text-tested',
  none: 'bg-sunken text-muted',
}

const DIRECTION: Record<WeekDimension['direction'], { text: string; tone: string }> = {
  closer: { text: '↗ toward the plan', tone: 'text-profit' },
  further: { text: '↘ away from the plan', tone: 'text-loss' },
  same: { text: '→ about the same', tone: 'text-muted' },
  on_target: { text: '✓ on target', tone: 'text-profit' },
  met: { text: '✓ met', tone: 'text-profit' },
  missed: { text: '✗ missed', tone: 'text-loss' },
  not_recorded: { text: 'not recorded', tone: 'text-faint' },
}

function tone(v: number | null): 'profit' | 'loss' | 'muted' {
  return v === null ? 'muted' : v >= 0 ? 'profit' : 'loss'
}

function signedMoney(v: string | number | null | undefined): string {
  const n = num(v as string | number | null)
  return n === null ? '—' : money(n, { sign: true, cents: false })
}

export function WeeklyReview({ onOpen, onToday }: { onOpen: (id: string) => void; onToday: () => void }) {
  const [end, setEnd] = useState<string | null>(null)
  const week = useAsync(() => api.week(end), [end])
  const weeks = useAsync(() => api.weeks(12), [])
  const w = week.data

  return (
    <div className="space-y-8 sm:space-y-10">
      <header className="rise">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="label">{w ? (w.in_progress ? 'This week so far' : 'Week in review') : 'Week in review'}</div>
          <button
            onClick={onToday}
            className="rounded-sm border border-line px-3 py-1 text-note text-muted hover:bg-hover hover:text-ink"
          >
            Today →
          </button>
        </div>
        <div className="mt-2 flex items-center gap-2 sm:gap-3">
          <button
            aria-label="The week before"
            disabled={!w}
            onClick={() => w && setEnd(w.previous)}
            className="rounded-sm border border-line px-2.5 py-1 text-copy text-muted hover:bg-hover hover:text-ink disabled:opacity-40"
          >
            ←
          </button>
          <h1 className="display min-w-0 flex-1 text-[24px] leading-snug sm:text-[32px]">
            {w ? `Week of ${w.label}` : 'Week in review'}
          </h1>
          <button
            aria-label="The week after"
            disabled={!w?.next}
            onClick={() => w?.next && setEnd(w.is_latest ? null : w.next)}
            className="rounded-sm border border-line px-2.5 py-1 text-copy text-muted hover:bg-hover hover:text-ink disabled:opacity-40"
          >
            →
          </button>
        </div>
        {w && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-body text-muted">
            <span
              className={`rounded-full px-2.5 py-0.5 text-caption font-semibold uppercase tracking-[0.06em] ${VERDICT_TONE[w.verdict.tone]}`}
            >
              {w.verdict.label}
            </span>
            <span>
              {w.verdict.text}
              {w.verdict.notes.length > 0 && ` · ${w.verdict.notes.join(' ')}`}
            </span>
          </div>
        )}
      </header>

      {weeks.data && (
        <WeekStrip rows={weeks.data} current={w?.monday ?? null} onPick={(r, latest) => setEnd(latest ? null : r.monday)} />
      )}

      {week.error ? (
        <ErrorPanel error={week.error} onRetry={week.reload} />
      ) : !w ? (
        <Loading label="Reading the week" />
      ) : (
        <WeekBody w={w} onOpen={onOpen} />
      )}
    </div>
  )
}

/* The last twelve weeks, oldest on the left, each coloured by whether it
   corrected the book. A row of them says more about a trading habit than any
   single week. */
function WeekStrip({
  rows,
  current,
  onPick,
}: {
  rows: WeekRow[]
  current: string | null
  onPick: (r: WeekRow, latest: boolean) => void
}) {
  const ordered = [...rows].reverse()
  const toward = rows.filter((r) => r.verdict.tone === 'good').length
  const judged = rows.filter((r) => r.verdict.tone !== 'none').length
  // The newest week sits on the right, so the strip opens scrolled to it.
  const strip = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const box = strip.current
    if (box) box.scrollLeft = box.scrollWidth
  }, [rows.length])
  return (
    <section>
      <SectionHeading
        title="Week by week"
        hint={`${toward} of ${judged} correcting weeks · tap one to open it`}
      />
      <div ref={strip} className="-mx-1 overflow-x-auto px-1 pb-1">
        <ol className="flex gap-2">
          {ordered.map((r, i) => {
            const gain = num(r.gain)
            const on = r.monday === current
            return (
              <li key={r.monday}>
                <button
                  onClick={() => onPick(r, i === ordered.length - 1)}
                  title={`${r.verdict.label}: ${r.verdict.text}`}
                  className={`flex w-[92px] flex-col rounded-sm border px-2.5 py-2 text-left transition-colors ${
                    on ? 'border-accent bg-accent-soft' : 'border-line hover:bg-hover'
                  }`}
                >
                  <span className="text-micro uppercase tracking-wider text-faint">{shortDate(r.monday)}</span>
                  <span className={`figure text-note font-semibold ${gain === null ? 'text-faint' : gain >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {gain === null ? '—' : money(gain, { sign: true, cents: false })}
                  </span>
                  <span className={`mt-1 h-1.5 w-full rounded-full ${VERDICT_TONE[r.verdict.tone].split(' ')[0]}`} />
                </button>
              </li>
            )
          })}
        </ol>
      </div>
    </section>
  )
}

function WeekBody({ w, onOpen }: { w: WeekReview; onOpen: (id: string) => void }) {
  const a = w.account
  const gain = num(a?.gain)
  const growth = num(a?.growth)
  const spy = num(a?.spy)
  const banked = num(w.banked)
  const bpStart = w.scorecard.find((d) => d.key === 'bp')
  const winRate = w.closed.length ? w.wins / w.closed.length : null

  return (
    <>
      {w.takeaways.length > 0 && (
        <section className={`${card} px-5 py-4 sm:px-6`}>
          <div className="mb-2 text-body font-medium uppercase tracking-wider text-muted">The week in short</div>
          <ul className="space-y-1.5 text-copy leading-relaxed text-ink">
            {w.takeaways.map((t) => (
              <li key={t} className="flex gap-2">
                <span aria-hidden className="text-faint">·</span>
                <span>{t}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="The account"
          value={gain === null ? '—' : money(gain, { sign: true, cents: false })}
          tone={tone(gain)}
          sub={
            growth === null
              ? 'no closes recorded'
              : `${pct(growth, 2, true)} · SPY ${spy === null ? '—' : pct(spy, 2, true)}`
          }
          title="Net liq from the close before Monday to the week's last close, money wired in or out taken out."
        />
        <StatTile
          label="Banked"
          value={banked === null ? '—' : money(banked, { sign: true, cents: false })}
          tone={tone(w.closed.length ? banked : null)}
          sub={
            w.closed.length
              ? `${w.closed.length} closed · ${pct(winRate, 0)} won`
              : 'nothing closed'
          }
        />
        <StatTile
          label="Opened"
          value={String(w.opened.length)}
          sub={`${w.rolls.length} roll${w.rolls.length === 1 ? '' : 's'} · fees ${money(num(w.fees), { cents: false })}`}
        />
        <StatTile
          label="Buying power"
          value={bpStart && bpStart.end_text !== '—' ? bpStart.end_text : '—'}
          sub={bpStart && bpStart.start_text !== '—' ? `from ${bpStart.start_text} · Tom 40–50%` : 'not recorded'}
        />
      </section>

      <WeekCharts w={w} />

      <section>
        <SectionHeading
          title="Closer to your plan?"
          hint="where the week found the book, where it left it"
          help="Each part of Tom's plan as you set it, read at the last close before Monday and at the week's last close. Toward the plan means the gap to the target shrank. Profit is judged against Tom's 2–3% a month, about 0.5% a week."
        />
        <Scorecard rows={w.scorecard} />
      </section>

      <section>
        <SectionHeading
          title="Allocation"
          hint={
            w.allocation_start.day || w.allocation_end.day
              ? `share of net liq · ${w.allocation_start.day ? shortDate(w.allocation_start.day) : '—'} → ${
                  w.allocation_end.day ? shortDate(w.allocation_end.day) : '—'
                }`
              : 'not recorded that week'
          }
          help="What each share of the plan tied up, as a share of net liq, against its limit: 11x 30%, PMCC 40%, spec 30% and hedges 5% of the buying power Tom allows (half of net liq)."
        />
        <AllocationTable start={w.allocation_start.rows} end={w.allocation_end.rows} />
      </section>

      <section>
        <SectionHeading title="Closed this week" hint="best first · tap one to open it" />
        {w.closed.length === 0 ? <Empty title="Nothing closed this week." /> : <ClosedList rows={w.closed} onOpen={onOpen} />}
      </section>

      <section>
        <SectionHeading title="Opened this week" hint="and the share of the plan each draws on" />
        {w.opened.length === 0 ? <Empty title="Nothing opened this week." /> : <OpenedList rows={w.opened} onOpen={onOpen} />}
      </section>

      {w.rolls.length > 0 && (
        <section>
          <SectionHeading title="Rolls" />
          <ul className={`${card} divide-y divide-line px-5`}>
            {w.rolls.map((r) => (
              <li key={`${r.id}-${r.at}`} className="py-3 text-body">
                <button onClick={() => onOpen(r.id)} className="font-medium text-ink hover:underline">
                  {r.product} · {r.name}
                </button>{' '}
                <span className="text-muted">
                  {shortDate(r.at)} · {signedMoney(r.credit)} · out: {r.closed.join(', ') || '—'} · in:{' '}
                  {r.opened.join(', ') || '—'}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {w.rules.length > 0 && (
        <section>
          <SectionHeading
            title="Rules on this week's trades"
            hint="Tom's rules, read on the trades that closed"
          />
          <ul className="grid gap-3 md:grid-cols-2">
            {w.rules.map((r) => (
              <li key={r.key} className={`${card} px-5 py-4`}>
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-copy font-medium text-ink">{r.title}</span>
                  <span className="figure shrink-0 text-note">
                    <span className="text-profit">{r.kept} kept</span>
                    {' · '}
                    <span className={r.broken ? 'text-loss' : 'text-faint'}>{r.broken} broken</span>
                  </span>
                </div>
                {r.broken_trades.length > 0 && (
                  <ul className="mt-2 space-y-1 text-note text-muted">
                    {r.broken_trades.slice(0, 4).map((t) => (
                      <li key={t.id}>
                        <button onClick={() => onOpen(t.id)} className="text-left hover:underline">
                          {t.product} {signedMoney(t.pnl)}
                          {t.why ? ` — ${t.why}` : ''}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {w.alerts.counts.length > 0 && (
        <section>
          <SectionHeading title="What the app flagged" hint="alerts raised during the week" />
          <div className={`${card} px-5 py-4`}>
            <div className="flex flex-wrap gap-2">
              {w.alerts.counts.map((c) => (
                <span key={c.kind} className="rounded-full border border-line px-3 py-1 text-note text-muted">
                  {c.label} · <span className="figure text-ink">{c.count}</span>
                </span>
              ))}
            </div>
            <ul className="mt-3 space-y-1 text-note text-muted">
              {w.alerts.lines.slice(0, 12).map((l, i) => (
                <li key={i}>
                  <span className="text-faint">{shortDate(l.at)}</span> · {l.summary}
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}

      {w.coming && (
        <section>
          <SectionHeading title="Next week" hint={w.coming.label} />
          <div className={`${card} px-5 py-4`}>
            <p className={`text-body ${w.coming.eleven_x.due ? 'font-medium text-accent' : 'text-muted'}`}>
              {w.coming.eleven_x.text}
            </p>
            {w.coming.items.length === 0 ? (
              <p className="mt-2 text-body text-faint">No expiry, 21-day mark or earnings falls next week.</p>
            ) : (
              <ul className="mt-2 divide-y divide-line">
                {w.coming.items.map((i) => (
                  <li key={`${i.id}-${i.what}-${i.day}`} className="flex items-baseline gap-3 py-2 text-body">
                    <span className="figure w-14 shrink-0 text-faint">{shortDate(i.day)}</span>
                    <button onClick={() => onOpen(i.id)} className="min-w-0 flex-1 text-left hover:underline">
                      <span className="font-medium text-ink">{i.underlying}</span>{' '}
                      <span className="text-muted">
                        {i.name} {i.what}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}
    </>
  )
}

/* Two small charts rather than one with two scales: the account against SPY
   on one percentage axis, then the book's buying power and theta day by day.
   A day the app did not run has no reading and is left as a gap. */
function WeekCharts({ w }: { w: WeekReview }) {
  const days = w.chart
  const [hover, setHover] = useState<number | null>(null)
  if (days.length < 2) {
    return (
      <section>
        <SectionHeading title="Day by day" />
        <Empty title="Not enough closes recorded to draw the week." />
      </section>
    )
  }
  const shown = hover === null ? null : days[hover]
  return (
    <section>
      <SectionHeading title="Day by day" hint="from the close before Monday" />
      <div className="grid gap-3 lg:grid-cols-2">
        <div className={`${card} px-4 py-3`}>
          <ChartHead
            legend={
              <>
                <Swatch className="bg-accent" /> You <Swatch className="ml-3 bg-line-strong" /> SPY
              </>
            }
            readout={
              shown
                ? `${shortDate(shown.day)} · you ${pct(num(shown.growth), 2, true)} · SPY ${
                    shown.spy === null ? '—' : pct(num(shown.spy), 2, true)
                  }`
                : ''
            }
          />
          <Lines
            series={[
              { values: days.map((d) => num(d.growth)), className: 'stroke-accent', width: 2 },
              { values: days.map((d) => num(d.spy)), className: 'stroke-line-strong', width: 1.5 },
            ]}
            labels={days.map((d) => d.day)}
            format={(v) => pct(v, 1, true)}
            zero
            hover={hover}
            onHover={setHover}
          />
        </div>
        <div className={`${card} px-4 py-3`}>
          <ChartHead
            legend={
              <>
                <Swatch className="bg-accent" /> Buying power used
              </>
            }
            readout={
              shown
                ? `${shortDate(shown.day)} · ${shown.bp_share === null ? 'not recorded' : pct(num(shown.bp_share), 0)}`
                : 'share of net liq · Tom 40–50%'
            }
          />
          <Lines
            series={[{ values: days.map((d) => num(d.bp_share)), className: 'stroke-accent', width: 2 }]}
            labels={days.map((d) => d.day)}
            format={(v) => pct(v, 0)}
            band={[0.4, 0.5]}
            hover={hover}
            onHover={setHover}
          />
          <ChartHead
            legend={
              <>
                <Swatch className="bg-profit" /> Theta a day
              </>
            }
            readout={
              shown
                ? `${shortDate(shown.day)} · ${shown.theta === null ? 'not recorded' : money(num(shown.theta), { cents: false })}`
                : ''
            }
          />
          <Lines
            series={[{ values: days.map((d) => num(d.theta)), className: 'stroke-profit', width: 2 }]}
            labels={days.map((d) => d.day)}
            format={(v) => money(v, { cents: false })}
            hover={hover}
            onHover={setHover}
          />
        </div>
      </div>
    </section>
  )
}

function Swatch({ className }: { className: string }) {
  return <span className={`mr-1.5 inline-block h-0.5 w-4 align-middle ${className}`} />
}

function ChartHead({ legend, readout }: { legend: ReactNode; readout: string }) {
  return (
    <div className="mt-1 flex flex-wrap items-baseline justify-between gap-x-3 text-note">
      <span className="text-muted">{legend}</span>
      <span className="num text-faint">{readout}</span>
    </div>
  )
}

const W = 520
const H = 120
const PAD = { top: 8, right: 8, bottom: 18, left: 48 }

function Lines({
  series,
  labels,
  format,
  zero = false,
  band,
  hover,
  onHover,
}: {
  series: { values: (number | null)[]; className: string; width: number }[]
  labels: string[]
  format: (v: number) => string
  zero?: boolean
  band?: [number, number]
  hover: number | null
  onHover: (i: number | null) => void
}) {
  const model = useMemo(() => {
    const all = series.flatMap((s) => s.values).filter((v): v is number => v !== null)
    if (all.length === 0) return null
    const extra = [...(zero ? [0] : []), ...(band ?? [])]
    let hi = Math.max(...all, ...extra)
    let lo = Math.min(...all, ...extra)
    if (hi === lo) {
      hi += Math.abs(hi) * 0.1 || 1
      lo -= Math.abs(lo) * 0.1 || 1
    }
    const n = labels.length
    const innerW = W - PAD.left - PAD.right
    const innerH = H - PAD.top - PAD.bottom
    const x = (i: number) => PAD.left + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW)
    const y = (v: number) => PAD.top + innerH - ((v - lo) / (hi - lo)) * innerH
    // A gap stays a gap: the line lifts over a day with no reading.
    const path = (vals: (number | null)[]) => {
      let out = ''
      let pen = false
      vals.forEach((v, i) => {
        if (v === null) {
          pen = false
          return
        }
        out += `${pen ? 'L' : 'M'}${x(i)},${y(v)} `
        pen = true
      })
      return out
    }
    return { x, y, hi, lo, innerW, n, paths: series.map((s) => path(s.values)) }
  }, [series, labels, zero, band])

  if (!model) return <div className="flex h-[90px] items-center justify-center text-note text-faint">Not recorded that week.</div>

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="mt-1 w-full"
      role="img"
      onMouseLeave={() => onHover(null)}
      onMouseMove={(e) => {
        const box = e.currentTarget.getBoundingClientRect()
        const rel = ((e.clientX - box.left) / box.width) * W
        const i = Math.round(((rel - PAD.left) / model.innerW) * (model.n - 1))
        onHover(Math.max(0, Math.min(model.n - 1, i)))
      }}
    >
      {band && (
        <rect
          x={PAD.left}
          width={W - PAD.left - PAD.right}
          y={model.y(band[1])}
          height={Math.max(model.y(band[0]) - model.y(band[1]), 0)}
          className="fill-profit-soft"
        />
      )}
      {zero && (
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={model.y(0)}
          y2={model.y(0)}
          className="stroke-line-strong"
          strokeDasharray="3 3"
        />
      )}
      {[model.hi, model.lo].map((v) => (
        <text key={v} x={PAD.left - 6} y={model.y(v) + 4} textAnchor="end" className="fill-faint text-[10px]">
          {format(v)}
        </text>
      ))}
      {labels.map((d, i) => (
        <text key={d} x={model.x(i)} y={H - 4} textAnchor="middle" className="fill-faint text-[10px]">
          {i === 0 ? 'before' : new Date(`${d}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short' })}
        </text>
      ))}
      {model.paths.map((d, k) => (
        <path
          key={k}
          d={d}
          fill="none"
          strokeWidth={series[k].width}
          strokeLinejoin="round"
          strokeLinecap="round"
          className={series[k].className}
        />
      ))}
      {series.map((s, k) =>
        s.values.map((v, i) =>
          v === null ? null : (
            <circle key={`${k}-${i}`} cx={model.x(i)} cy={model.y(v)} r={hover === i ? 3.5 : 2} className={`${s.className.replace('stroke-', 'fill-')}`} />
          ),
        ),
      )}
      {hover !== null && (
        <line x1={model.x(hover)} x2={model.x(hover)} y1={PAD.top} y2={H - PAD.bottom} className="stroke-line-strong" />
      )}
    </svg>
  )
}

function Scorecard({ rows }: { rows: WeekDimension[] }) {
  return (
    <ul className={`${card} divide-y divide-line px-5`}>
      {rows.map((r) => {
        const d = DIRECTION[r.direction]
        return (
          <li key={r.key} className="grid grid-cols-1 gap-1 py-3 sm:grid-cols-[180px_1fr_auto] sm:items-baseline sm:gap-4">
            <div>
              <div className="text-copy font-medium text-ink">{r.label}</div>
              <div className="text-caption text-faint">{r.target}</div>
            </div>
            <div className="figure text-copy text-ink">
              {r.key === 'profit' ? (r.end_text || '—') : `${r.start_text} → ${r.end_text}`}
              {r.note && <span className="ml-2 font-sans text-caption text-faint">{r.note}</span>}
            </div>
            <div className={`text-body font-medium ${d.tone}`}>{d.text}</div>
          </li>
        )
      })}
    </ul>
  )
}

function AllocationTable({ start, end }: { start: WeekBucket[]; end: WeekBucket[] }) {
  const names = Array.from(new Set([...start, ...end].map((r) => r.bucket)))
  if (names.length === 0) return <Empty title="No buying power was recorded that week." />
  const by = (rows: WeekBucket[], k: string) => rows.find((r) => r.bucket === k) ?? null
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-copy">
        <thead>
          <tr className="border-b border-line text-left text-caption uppercase tracking-wider text-faint">
            <th className="w-full py-3 pl-5 pr-3 font-medium">Share</th>
            <th className="py-3 pr-3 text-right font-medium">Start</th>
            <th className="py-3 pr-3 text-right font-medium">End</th>
            <th className="py-3 pr-5 text-right font-medium">Limit</th>
          </tr>
        </thead>
        <tbody>
          {names.map((k) => {
            const a = by(start, k)
            const b = by(end, k)
            const limit = num((b ?? a)?.limit)
            const over = num(b?.over) ?? 0
            return (
              <tr key={k} className="border-b border-line/60 last:border-0">
                <td className="py-2.5 pl-5 pr-3 text-ink">{k}</td>
                <td className="figure whitespace-nowrap py-2.5 pr-3 text-right text-muted">{a ? pct(num(a.share), 1) : '—'}</td>
                <td className={`figure whitespace-nowrap py-2.5 pr-3 text-right ${over > 0 ? 'text-loss' : 'text-ink'}`}>
                  {b ? pct(num(b.share), 1) : '—'}
                </td>
                <td className="figure whitespace-nowrap py-2.5 pr-5 text-right text-faint">
                  {limit === null ? '—' : limit === 0 ? 'none' : pct(limit, 0)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function ClosedList({ rows, onOpen }: { rows: WeekClosed[]; onOpen: (id: string) => void }) {
  const narrow = useNarrow()
  if (narrow) {
    return (
      <ul className="space-y-2">
        {rows.map((r) => (
          <li key={r.id}>
            <button onClick={() => onOpen(r.id)} className={`${card} block w-full px-4 py-3 text-left`}>
              <div className="flex items-baseline gap-2">
                <span className="text-copy font-semibold text-ink">{r.underlying}</span>
                <span className="min-w-0 flex-1 truncate text-body text-muted">{r.name}</span>
                <span className={`figure shrink-0 text-copy font-semibold ${num(r.pnl)! >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {signedMoney(r.pnl)}
                </span>
              </div>
              <div className="mt-1 text-note text-muted">
                {r.ended} · {r.days}d held{r.captured !== null ? ` · ${pct(num(r.captured), 0)} of max` : ''}
                {r.over_2pct && <span className="ml-1 text-loss">· over 2% of net liq</span>}
              </div>
            </button>
          </li>
        ))}
      </ul>
    )
  }
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-copy">
        <thead>
          <tr className="border-b border-line text-left text-caption uppercase tracking-wider text-faint">
            <th className="py-3 pl-5 pr-4 font-medium">Trade</th>
            <th className="w-full py-3 pr-4 font-medium">How it ended</th>
            <th className="py-3 pr-4 text-right font-medium">Held</th>
            <th className="py-3 pr-4 text-right font-medium">Of max</th>
            <th className="py-3 pr-5 text-right font-medium">P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} onClick={() => onOpen(r.id)} className="cursor-pointer border-b border-line/60 last:border-0 hover:bg-hover">
              <td className="whitespace-nowrap py-2.5 pl-5 pr-4">
                <div className="font-semibold text-ink">{r.underlying}</div>
                <div className="text-caption text-faint">{r.name}</div>
              </td>
              <td className="py-2.5 pr-4 text-muted">
                {r.ended}
                {r.over_2pct && <span className="ml-2 text-loss">over 2% of net liq</span>}
              </td>
              <td className="figure whitespace-nowrap py-2.5 pr-4 text-right text-muted">{r.days}d</td>
              <td className="figure whitespace-nowrap py-2.5 pr-4 text-right text-muted">
                {r.captured === null ? '—' : pct(num(r.captured), 0)}
              </td>
              <td className={`figure whitespace-nowrap py-2.5 pr-5 text-right font-semibold ${num(r.pnl)! >= 0 ? 'text-profit' : 'text-loss'}`}>
                {signedMoney(r.pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function OpenedList({ rows, onOpen }: { rows: WeekOpened[]; onOpen: (id: string) => void }) {
  const narrow = useNarrow()
  const credit = (r: WeekOpened) => {
    const c = num(r.credit)
    return c === null ? '—' : `${money(Math.abs(c), { cents: false })} ${c >= 0 ? 'credit' : 'debit'}`
  }
  if (narrow) {
    return (
      <ul className="space-y-2">
        {rows.map((r) => (
          <li key={r.id}>
            <button onClick={() => onOpen(r.id)} className={`${card} block w-full px-4 py-3 text-left`}>
              <div className="flex items-baseline gap-2">
                <span className="text-copy font-semibold text-ink">{r.underlying}</span>
                <span className="min-w-0 flex-1 truncate text-body text-muted">{r.name}</span>
                <span className="shrink-0 text-note text-faint">{shortDate(r.opened)}</span>
              </div>
              <div className="mt-1 text-note text-muted">
                {credit(r)} · {r.dte === null ? '—' : `${r.dte} DTE`} · {r.bucket}
                {r.bucket_over && <span className="text-loss"> (over its limit)</span>}
                {!r.open && ' · already closed'}
              </div>
            </button>
          </li>
        ))}
      </ul>
    )
  }
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-copy">
        <thead>
          <tr className="border-b border-line text-left text-caption uppercase tracking-wider text-faint">
            <th className="py-3 pl-5 pr-4 font-medium">Trade</th>
            <th className="py-3 pr-4 font-medium">Opened</th>
            <th className="py-3 pr-4 text-right font-medium">Credit</th>
            <th className="py-3 pr-4 text-right font-medium">DTE</th>
            <th className="w-full py-3 pr-4 font-medium">Share of the plan</th>
            <th className="py-3 pr-5 text-right font-medium">P&amp;L now</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} onClick={() => onOpen(r.id)} className="cursor-pointer border-b border-line/60 last:border-0 hover:bg-hover">
              <td className="whitespace-nowrap py-2.5 pl-5 pr-4">
                <div className="font-semibold text-ink">{r.underlying}</div>
                <div className="text-caption text-faint">{r.name}</div>
              </td>
              <td className="figure whitespace-nowrap py-2.5 pr-4 text-muted">{shortDate(r.opened)}</td>
              <td className="figure whitespace-nowrap py-2.5 pr-4 text-right text-ink">{credit(r)}</td>
              <td className="figure whitespace-nowrap py-2.5 pr-4 text-right text-muted">{r.dte ?? '—'}</td>
              <td className="py-2.5 pr-4 text-muted">
                {r.bucket}
                {r.bucket_over && <span className="ml-2 text-loss">over its limit</span>}
                {!r.open && <span className="ml-2 text-faint">already closed</span>}
              </td>
              <td className={`figure whitespace-nowrap py-2.5 pr-5 text-right ${num(r.pnl) === null ? 'text-faint' : num(r.pnl)! >= 0 ? 'text-profit' : 'text-loss'}`}>
                {r.pnl === null ? 'not quoted' : signedMoney(r.pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
