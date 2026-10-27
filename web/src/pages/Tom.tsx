import { useEffect, useState, type ReactNode } from 'react'
import { Loading, ErrorPanel, SectionHeading, Empty } from '../components/States'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { money, pct, num, shortDate, fullDate, relativeTime, EM_DASH } from '../lib/format'
import type {
  TomChecklistItem,
  TomGauge,
  TomHistoryRule,
  RuleTrade,
  TomPosition,
  TomRegime,
  TomReport,
  TomStatus,
} from '../types'

/* Tom Analysis: the book held up against Tom King's 2026 trading plan.

   The page answers one question — "how do I trade more like Tom?" — in the
   order a trader needs it: how many of his rules the book keeps right now and
   the three gaps that matter most, then the book's dials against his limits,
   the chart the way he reads it, every open position under his size, stop and
   target, what the history says about each of his rules, his plan scaled to
   this account, and his nightly review with tonight's numbers in it.

   Every judgement is the backend's (core/tom.py) and every one is a rule, the
   measurement and the gap, in words. This file only draws them. */

/* ---- tone ---------------------------------------------------------------- */

const TONE: Record<TomStatus | 'good', { chip: string; dot: string; text: string; word: string }> = {
  ok: { chip: 'bg-profit-soft text-ok', dot: 'bg-ok', text: 'text-ok', word: 'Within' },
  good: { chip: 'bg-profit-soft text-ok', dot: 'bg-ok', text: 'text-ok', word: 'Good' },
  watch: { chip: 'bg-tested-soft text-tested', dot: 'bg-tested', text: 'text-tested', word: 'Watch' },
  breach: { chip: 'bg-danger-soft text-danger', dot: 'bg-danger', text: 'text-danger', word: 'Over' },
  info: { chip: 'bg-sunken text-muted', dot: 'bg-line-strong', text: 'text-muted', word: 'Info' },
  unknown: { chip: 'bg-sunken text-faint', dot: 'bg-line-strong', text: 'text-faint', word: 'Not measured' },
}

const REGIME_TONE: Record<TomRegime['label'], { chip: string; text: string; dot: string }> = {
  Bullish: { chip: 'bg-profit-soft text-ok', text: 'text-ok', dot: 'bg-ok' },
  Neutral: { chip: 'bg-watch-soft text-watch', text: 'text-watch', dot: 'bg-watch' },
  Bearish: { chip: 'bg-danger-soft text-danger', text: 'text-danger', dot: 'bg-danger' },
}

const FIT: Record<'with' | 'caution' | 'against', { label: string; tone: TomStatus }> = {
  with: { label: 'With the chart', tone: 'ok' },
  caution: { label: 'Caution', tone: 'watch' },
  against: { label: 'Against the chart', tone: 'breach' },
}

function Chip({ status, children }: { status: TomStatus | 'good'; children?: ReactNode }) {
  const t = TONE[status]
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[12px] font-semibold uppercase tracking-[0.06em] ${t.chip}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${t.dot}`} />
      {children ?? t.word}
    </span>
  )
}

const card = 'surface sheened'

/* A value that animates in from its resting point once, on arrival, so a
   bar reads as a measurement being taken rather than a shape painted on. */
function useArrived(): boolean {
  const [on, setOn] = useState(false)
  useEffect(() => {
    const id = requestAnimationFrame(() => setOn(true))
    return () => cancelAnimationFrame(id)
  }, [])
  return on
}

/* ---- the score ring -------------------------------------------------------- */

function ScoreRing({ kept, judged }: { kept: number; judged: number }) {
  const arrived = useArrived()
  const share = judged ? kept / judged : 0
  const r = 74
  const c = 2 * Math.PI * r
  const color = share >= 0.75 ? 'var(--ok)' : share >= 0.5 ? 'var(--accent)' : share >= 0.34 ? 'var(--tested)' : 'var(--danger)'
  return (
    <div className="relative h-[184px] w-[184px] shrink-0" role="img" aria-label={`${kept} of ${judged} rules kept`}>
      <svg viewBox="0 0 184 184" className="h-full w-full -rotate-90">
        <circle cx="92" cy="92" r={r} fill="none" stroke="var(--border)" strokeWidth="12" />
        <circle
          cx="92"
          cy="92"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={arrived ? c * (1 - share) : c}
          style={{ transition: 'stroke-dashoffset 1100ms var(--ease)' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div className="figure text-[52px] leading-none">{kept}</div>
        <div className="mt-1 text-[14px] text-muted">of {judged} rules kept</div>
      </div>
    </div>
  )
}

/* ---- a horizontal scale with Tom's zones painted on it ------------------- */

type Zone = { from: number; to: number; className: string; label?: string }

function ZoneBar({
  min,
  max,
  zones,
  value,
  marks = [],
  label,
}: {
  min: number
  max: number
  zones: Zone[]
  value: number | null
  marks?: { at: number; label: string }[]
  label: string
}) {
  const arrived = useArrived()
  const at = (v: number) => `${((Math.min(Math.max(v, min), max) - min) / (max - min)) * 100}%`
  const off = value !== null && (value > max || value < min)
  return (
    <div role="img" aria-label={label}>
      <div className="relative h-3 w-full overflow-hidden rounded-full bg-sunken">
        {zones.map((z, i) => (
          <div
            key={i}
            className={`absolute inset-y-0 ${z.className}`}
            style={{ left: at(z.from), width: `calc(${at(z.to)} - ${at(z.from)})` }}
          />
        ))}
      </div>
      <div className="relative h-0">
        {value !== null && (
          <div
            className="absolute -top-[18px] h-6 w-[5px] -translate-x-1/2 rounded-full bg-ink shadow-[0_0_0_3px_var(--bg-raised-solid)]"
            style={{
              left: arrived ? at(value) : at(min),
              transition: 'left 900ms var(--ease)',
            }}
            title={off ? 'Off the scale' : undefined}
          />
        )}
      </div>
      {marks.length > 0 && (
        <div className="relative mt-2 h-4 text-[12px] text-faint">
          {marks.map((m) => (
            <span key={m.label} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: at(m.at) }}>
              {m.label}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function gaugeBar(g: TomGauge) {
  const v = num(g.value)
  const lo = num(g.low)
  const hi = num(g.high)
  const top = num(g.scale_max) ?? 1
  if (g.key === 'bp') {
    return (
      <ZoneBar
        label={`Buying power ${g.display}, Tom's target 40 to 50 percent`}
        min={0}
        max={1}
        value={v}
        zones={[
          { from: 0.4, to: 0.5, className: 'bg-ok/45' },
          { from: 0.5, to: 0.6, className: 'bg-tested/30' },
          { from: 0.6, to: 0.85, className: 'bg-danger/25' },
          { from: 0.85, to: 1, className: 'bg-danger/45' },
        ]}
        marks={[
          { at: 0.4, label: '40' },
          { at: 0.5, label: '50' },
          { at: 0.6, label: '60' },
          { at: 0.85, label: '85%' },
        ]}
      />
    )
  }
  if (g.key === 'delta') {
    const limit = hi ?? 1
    return (
      <ZoneBar
        label={`Delta ${g.display}, Tom's limit ${g.target}`}
        min={-top}
        max={top}
        value={v}
        zones={[{ from: -limit, to: limit, className: 'bg-ok/40' }, { from: -0.5, to: 0.5, className: 'bg-line-strong' }]}
        marks={[
          { at: -limit, label: `-${Math.round(limit)}` },
          { at: 0, label: '0' },
          { at: limit, label: `+${Math.round(limit)}` },
        ]}
      />
    )
  }
  if (g.key === 'theta') {
    return (
      <ZoneBar
        label={`Theta ${g.display}, Tom's target ${g.target}`}
        min={0}
        max={top}
        value={v}
        zones={[
          { from: lo ?? 0.003, to: top, className: 'bg-ok/25' },
          { from: lo ?? 0.003, to: hi ?? 0.004, className: 'bg-ok/45' },
        ]}
        marks={[
          { at: lo ?? 0.003, label: '0.3%' },
          { at: hi ?? 0.004, label: '0.4%' },
        ]}
      />
    )
  }
  return (
    <ZoneBar
      label={`Vega ${g.display} of theta, Tom's limit ${g.target}`}
      min={0}
      max={top}
      value={v}
      zones={[
        { from: 0, to: hi ?? 1.5, className: 'bg-ok/45' },
        { from: hi ?? 1.5, to: 3, className: 'bg-tested/25' },
        { from: 3, to: top, className: 'bg-danger/25' },
      ]}
      marks={[
        { at: 1, label: '1×' },
        { at: hi ?? 1.5, label: '1.5×' },
        { at: 3, label: '3×' },
      ]}
    />
  )
}

function GaugeCard({ g }: { g: TomGauge }) {
  return (
    <div className={`${card} lift flex h-full flex-col px-6 py-5`}>
      <div className="flex items-start justify-between gap-3">
        <div className="label">{g.label}</div>
        <Chip status={g.status} />
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className={`figure text-[34px] leading-none ${TONE[g.status].text === 'text-faint' ? 'text-faint' : 'text-ink'}`}>
          {g.display}
        </span>
        <span className="text-[13px] text-muted">Tom: {g.target}</span>
      </div>
      <div className="mt-6">{gaugeBar(g)}</div>
      <p className="mt-4 text-[14px] leading-relaxed text-muted">{g.text}</p>
    </div>
  )
}

/* ---- the regime ------------------------------------------------------------ */

function ConditionRow({ met, label, detail }: { met: boolean; label: string; detail: string }) {
  return (
    <li className="flex items-center gap-3 py-2">
      <span
        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[13px] font-bold ${
          met ? 'bg-profit-soft text-ok' : 'bg-danger-soft text-danger'
        }`}
        aria-label={met ? 'met' : 'not met'}
      >
        {met ? '✓' : '✕'}
      </span>
      <span className="min-w-0 flex-1 text-[15px]">{label}</span>
      <span className="num text-[13px] text-muted">{detail}</span>
    </li>
  )
}

function MarketCard({ market }: { market: TomRegime }) {
  const tone = REGIME_TONE[market.label]
  return (
    <div className={`${card} relative overflow-hidden px-7 py-6`}>
      <div
        aria-hidden
        className={`pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full opacity-25 blur-3xl ${tone.dot}`}
      />
      <div className="label">S&amp;P 500 · SPY daily chart</div>
      <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className={`display text-[44px] ${tone.text}`}>{market.label}</span>
        <span className="text-[15px] text-muted">
          {market.bullish} of 4 tests bullish · close {market.price.toFixed(2)} on {shortDate(market.as_of)}
        </span>
      </div>
      <ul className="mt-4 divide-y divide-line">
        {market.conditions.map((c) => (
          <ConditionRow key={c.key} met={c.met} label={c.label} detail={c.detail} />
        ))}
      </ul>
      <div className="mt-5">
        <div className="label">Tom focuses on</div>
        <div className="mt-2 flex flex-wrap gap-2">
          {market.focus.map((f) => (
            <span key={f} className={`rounded-full px-3 py-1 text-[14px] font-medium ${tone.chip}`}>
              {f}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}

function ProductCard({ regime, positions }: { regime: TomRegime; positions: TomPosition[] }) {
  const tone = REGIME_TONE[regime.label]
  return (
    <div className={`${card} lift flex h-full flex-col px-5 py-4`}>
      <div className="flex items-center justify-between gap-2">
        <div className="display text-[20px]">{regime.product}</div>
        <span className={`rounded-full px-2.5 py-0.5 text-[12px] font-semibold uppercase tracking-[0.06em] ${tone.chip}`}>
          {regime.label}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-1.5" aria-label={`${regime.bullish} of 4 tests bullish`}>
        {regime.conditions.map((c) => (
          <span
            key={c.key}
            title={`${c.label}: ${c.met ? 'yes' : 'no'} (${c.detail})`}
            className={`h-2 flex-1 rounded-full ${c.met ? 'bg-ok' : 'bg-danger/60'}`}
          />
        ))}
      </div>
      <p className="mt-3 text-[13px] text-muted">{regime.put_zone}</p>
      {positions.length > 0 && (
        <ul className="mt-auto space-y-1.5 border-t border-line pt-3">
          {positions.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-2 text-[14px]">
              <span className="truncate">{p.label}</span>
              {p.fit ? <Chip status={FIT[p.fit].tone}>{FIT[p.fit].label}</Chip> : <span className="text-[12px] text-faint">not judged</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/* ---- positions -------------------------------------------------------------- */

function Meter({ value, cap, tone }: { value: number | null; cap?: number; tone: string }) {
  const arrived = useArrived()
  if (value === null) return <span className="text-faint">{EM_DASH}</span>
  const scale = Math.max(cap ? cap * 3 : 1, value * 1.05, 0.0001)
  return (
    <div className="relative h-2 w-full min-w-[84px] overflow-hidden rounded-full bg-sunken">
      <div
        className={`absolute inset-y-0 left-0 rounded-full ${tone}`}
        style={{ width: arrived ? `${(Math.min(value, scale) / scale) * 100}%` : '0%', transition: 'width 800ms var(--ease)' }}
      />
      {cap !== undefined && (
        <div className="absolute inset-y-0 w-[2px] bg-ink/60" style={{ left: `${(cap / scale) * 100}%` }} title="Tom's cap" />
      )}
    </div>
  )
}

/* Only what is dangerous or needs doing: a bad entry (too big, against the
   chart the day it opened) or bad management (past or near the stop, at the
   target, inside 21 DTE, rolled, added to). Facts that say nothing about either
   — slightly over size, where the chart has gone since — are left out here. */
const ATTENTION_WATCH = new Set(['stop', 'dte', 'roll', 'entry'])
function needsAttention(f: TomPosition['flags'][number]): boolean {
  if (f.level === 'breach') return true
  if (f.level === 'watch') return ATTENTION_WATCH.has(f.code)
  return f.level === 'good' && f.code === 'target'
}
const HIDDEN_KEY = 'td.tom.hidden'
const flagKey = (p: TomPosition, f: TomPosition['flags'][number]) => `${p.id}|${f.code}|${f.level}`

/* A message he has read and set aside: pushed down under the rest, never gone.
   Kept per message and level, so a stop that moves from near to past comes back. */
function useHidden(): [Set<string>, (key: string) => void] {
  const [hidden, setHidden] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? '[]') as string[])
    } catch {
      return new Set()
    }
  })
  function toggle(key: string) {
    setHidden((was) => {
      const next = new Set(was)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      try {
        localStorage.setItem(HIDDEN_KEY, JSON.stringify([...next]))
      } catch {
        /* remembered as a convenience only */
      }
      return next
    })
  }
  return [hidden, toggle]
}

function PositionsTable({ positions }: { positions: TomPosition[] }) {
  const [hidden, toggleHidden] = useHidden()
  const [showPushed, setShowPushed] = useState<Set<string>>(new Set())
  if (positions.length === 0) return <Empty title="Nothing open." />
  const split = (p: TomPosition) => {
    const shown = p.flags.filter(needsAttention)
    return {
      active: shown.filter((f) => !hidden.has(flagKey(p, f))),
      pushed: shown.filter((f) => hidden.has(flagKey(p, f))),
    }
  }
  // What still needs attention first; a row with everything set aside goes down.
  const rows = [...positions].sort((a, b) => Number(split(b).active.length > 0) - Number(split(a).active.length > 0))
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-[15px]">
        <thead>
          <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
            <th className="py-3 pl-5 pr-4 font-medium">Position</th>
            <th className="py-3 pr-4 text-right font-medium">P&amp;L</th>
            <th className="py-3 pr-4 font-medium" title="What you lose if Tom's stop is hit, as a share of your account, against the most he allows">
              Loss at Tom's stop
            </th>
            <th className="py-3 pr-4 font-medium">To Tom's stop</th>
            <th className="py-3 pr-4 font-medium">To target</th>
            <th className="py-3 pr-4 font-medium">Chart</th>
            <th className="w-full py-3 pr-5 font-medium">Needs attention</th>
          </tr>
        </thead>
        <tbody className="rows align-top">
          {rows.map((p) => {
            const { active, pushed } = split(p)
            const size = num(p.loss_at_stop_share)
            const cap = num(p.size_cap) ?? 0.02
            const stop = num(p.stop_progress)
            const target = num(p.target_progress)
            const pnl = num(p.open_pnl)
            const sizeTone = size === null ? 'bg-line-strong' : size > cap * 1.25 ? 'bg-danger' : size > cap ? 'bg-tested' : 'bg-ok'
            const stopTone = stop === null ? 'bg-line-strong' : stop >= 1 ? 'bg-danger' : stop >= 0.75 ? 'bg-tested' : 'bg-watch'
            return (
              <tr key={p.id} className="border-b border-line/60 last:border-0 hover:bg-hover">
                <td className="py-4 pl-5 pr-4">
                  <div className="whitespace-nowrap font-semibold">{p.label}</div>
                  <div className="whitespace-nowrap text-[13px] text-muted">
                    {p.underlying} · {p.playbook_name}
                    {p.analog && <span title="Tom has no rule for this shape; checked against his nearest one"> (by analogy)</span>}
                  </div>
                  <div className="whitespace-nowrap text-[12px] text-faint">
                    {p.tier === 'core' ? 'Core' : p.tier === 'spec' ? 'Spec' : p.tier === 'hedge' ? 'Hedge' : "Not in Tom's plan"}
                    {p.dte !== null && ` · ${p.dte} DTE`}
                    {p.rolls > 0 && ` · rolled ${p.rolls}×`}
                  </div>
                </td>
                <td className="num whitespace-nowrap py-4 pr-4 text-right">
                  <div className={pnl === null ? 'text-faint' : pnl >= 0 ? 'text-profit' : 'text-loss'}>
                    {money(p.open_pnl, { sign: true, cents: false })}
                  </div>
                  {p.pct_of_credit !== null && num(p.credit)! > 0 && (
                    <div className="text-[12px] text-faint">{pct(p.pct_of_credit, 0, true)} of credit</div>
                  )}
                </td>
                <td className="py-4 pr-4">
                  <Meter value={size} cap={cap} tone={sizeTone} />
                  <div className="num mt-1.5 whitespace-nowrap text-[12px] text-muted">
                    {size === null ? 'cannot be sized' : `${pct(size, 1)} of account · max ${pct(cap, 1)}`}
                  </div>
                </td>
                <td className="py-4 pr-4">
                  <Meter value={stop} tone={stopTone} />
                  <div className="num mt-1.5 whitespace-nowrap text-[12px] text-muted">
                    {p.stop_cost !== null
                      ? `close at ${money(p.stop_cost, { cents: false })}`
                      : stop !== null
                        ? `${pct(stop, 0)} of 30% stop`
                        : 'no Tom stop'}
                  </div>
                </td>
                <td className="py-4 pr-4">
                  <Meter value={target === null ? null : Math.max(target, 0)} tone="bg-ok" />
                  <div className="num mt-1.5 whitespace-nowrap text-[12px] text-muted">
                    {p.target !== null ? `${pct(p.target, 0)} target` : 'no target'}
                  </div>
                </td>
                <td className="py-4 pr-4">
                  {p.fit ? <Chip status={FIT[p.fit].tone}>{FIT[p.fit].label}</Chip> : <span className="text-[13px] text-faint">not judged</span>}
                  {p.regime && <div className="mt-1.5 whitespace-nowrap text-[12px] text-faint">{p.product} {p.regime.toLowerCase()}</div>}
                </td>
                <td className="py-4 pr-5">
                  {active.length === 0 ? (
                    <span className="text-[14px] text-faint">Nothing that needs attention.</span>
                  ) : (
                    <ul className="space-y-1.5">
                      {active.map((f) => (
                        <li key={flagKey(p, f)} className="group flex gap-2 text-[14px] leading-snug">
                          <span className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full ${TONE[f.level].dot}`} />
                          <span className={`flex-1 ${f.level === 'breach' ? 'text-ink' : 'text-muted'}`}>{f.text}</span>
                          <button
                            onClick={() => toggleHidden(flagKey(p, f))}
                            title="Already known, or wrong: push it down under the rest"
                            className="shrink-0 self-start rounded-sm px-1.5 text-[12px] text-faint hover:bg-hover hover:text-ink"
                          >
                            Hide
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {pushed.length > 0 && (
                    <div className="mt-2">
                      <button
                        onClick={() =>
                          setShowPushed((was) => {
                            const next = new Set(was)
                            if (next.has(p.id)) next.delete(p.id)
                            else next.add(p.id)
                            return next
                          })
                        }
                        className="text-[12px] text-faint hover:text-ink"
                      >
                        {showPushed.has(p.id) ? '▾' : '▸'} {pushed.length} hidden
                      </button>
                      {showPushed.has(p.id) && (
                        <ul className="mt-1 space-y-1 opacity-60">
                          {pushed.map((f) => (
                            <li key={flagKey(p, f)} className="flex gap-2 text-[13px] leading-snug">
                              <span className={`mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full ${TONE[f.level].dot}`} />
                              <span className="flex-1 text-muted">{f.text}</span>
                              <button
                                onClick={() => toggleHidden(flagKey(p, f))}
                                className="shrink-0 self-start rounded-sm px-1.5 text-[12px] text-faint hover:bg-hover hover:text-ink"
                              >
                                Show
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/* ---- history ---------------------------------------------------------------- */

function Side({ label, n, pnl, winRate, bad }: { label: string; n: number; pnl: string; winRate: number | null; bad?: boolean }) {
  const p = num(pnl) ?? 0
  return (
    <div className={`rounded-sm px-3 py-2.5 ${bad ? 'bg-danger-soft/60' : 'bg-sunken/70'}`}>
      <div className="text-[12px] font-medium text-muted">{label}</div>
      <div className={`num mt-1 text-[20px] font-semibold ${n === 0 ? 'text-faint' : p >= 0 ? 'text-profit' : 'text-loss'}`}>
        {n === 0 ? EM_DASH : money(pnl, { sign: true, cents: false })}
      </div>
      <div className="num text-[12px] text-faint">
        {n} trades{winRate !== null && n > 0 ? ` · ${pct(winRate, 0)} won` : ''}
      </div>
    </div>
  )
}

/* One closed trade under a rule: what it was, how it went, and why the rule
   counted it on this side. */
function TradeLine({ t }: { t: RuleTrade }) {
  const p = num(t.pnl) ?? 0
  const credit = num(t.net_credit) ?? 0
  const facts = [
    `${shortDate(t.opened)} → ${t.closed ? shortDate(t.closed) : 'open'}`,
    t.days !== null ? `${t.days} days held` : null,
    t.dte_at_entry !== null ? `opened at ${t.dte_at_entry} DTE` : null,
    credit ? `${credit > 0 ? 'credit' : 'debit'} ${money(Math.abs(credit), { cents: false })}` : null,
    t.rolls ? `rolled ${t.rolls}×` : null,
  ].filter(Boolean)
  return (
    <li className="py-2.5">
      <div className="flex items-baseline gap-2">
        <span className="text-[15px] font-semibold">{t.product}</span>
        <span className="min-w-0 flex-1 truncate text-[13px] text-muted">{t.playbook}</span>
        <span className={`figure shrink-0 text-[15px] font-semibold ${p >= 0 ? 'text-profit' : 'text-loss'}`}>
          {money(t.pnl, { sign: true, cents: false })}
        </span>
      </div>
      {t.why && <div className="mt-0.5 text-[13px] text-ink/85">{t.why}</div>}
      <div className="num mt-0.5 text-[12px] text-faint">{facts.join(' · ')}</div>
      {t.legs && <div className="num mt-0.5 truncate text-[12px] text-faint" title={t.legs}>{t.legs}</div>}
    </li>
  )
}

/* One side of a rule, its trades best first. With more trades than shown,
   the list is the best few and the worst few. */
function TradeSide({ label, total, trades, bad }: { label: string; total: number; trades: RuleTrade[]; bad?: boolean }) {
  const split = total > trades.length
  const half = Math.ceil(trades.length / 2)
  const group = (title: string, rows: RuleTrade[]) => (
    <div className="mt-2">
      <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-faint">{title}</div>
      <ul className="divide-y divide-line">
        {rows.map((t) => (
          <TradeLine key={t.id} t={t} />
        ))}
      </ul>
    </div>
  )
  return (
    <div className={`rounded-sm px-4 py-3 ${bad ? 'bg-danger-soft/40' : 'bg-sunken/60'}`}>
      <div className="text-[13px] font-medium text-muted">
        {label} <span className="num text-faint">· {total}</span>
      </div>
      {trades.length === 0 ? (
        <div className="mt-2 text-[13px] text-faint">No trades.</div>
      ) : split ? (
        <>
          {group('Best', trades.slice(0, half))}
          {group('Worst', trades.slice(half).reverse())}
        </>
      ) : (
        group('Best to worst', trades)
      )}
    </div>
  )
}

function HistoryCard({ rule }: { rule: TomHistoryRule }) {
  const [open, setOpen] = useState(false)
  const any = rule.kept_trades.length + rule.broken_trades.length > 0
  return (
    <div className={`${card} lift flex h-full flex-col px-6 py-5 ${open ? 'md:col-span-2 xl:col-span-4' : ''}`}>
      <div className="flex items-start justify-between gap-3">
        <h3 className="display text-[19px]">{rule.title}</h3>
        <Chip status={rule.status}>{rule.status === 'breach' ? 'Costing you' : rule.status === 'ok' ? 'Kept' : rule.status === 'info' ? 'Info' : 'Mixed'}</Chip>
      </div>
      <div className="mt-0.5 text-[13px] text-faint">{rule.rule}</div>
      <p className="mt-3 text-[15px] leading-relaxed">{rule.headline}</p>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Side label={rule.kept_label} n={rule.kept} pnl={rule.pnl_kept} winRate={rule.win_rate_kept} />
        <Side label={rule.broken_label} n={rule.broken} pnl={rule.pnl_broken} winRate={rule.win_rate_broken} bad={rule.status === 'breach'} />
      </div>
      {rule.note && <p className="mt-3 text-[12px] leading-relaxed text-faint">{rule.note}</p>}
      {any && (
        <button
          onClick={() => setOpen(!open)}
          className="mt-auto self-start pt-3 text-[13px] font-medium text-accent hover:underline"
        >
          {open ? 'Hide the trades' : 'Show the best and worst trades'}
        </button>
      )}
      {open && (
        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <TradeSide label={rule.kept_label} total={rule.kept} trades={rule.kept_trades} />
          <TradeSide label={rule.broken_label} total={rule.broken} trades={rule.broken_trades} bad={rule.status === 'breach'} />
        </div>
      )}
    </div>
  )
}

/* ---- allocation --------------------------------------------------------------- */

const BUCKET_COLOR: Record<string, string> = {
  '11x': 'bg-accent',
  PMCC: 'bg-[#5e5ce6]',
  Spec: 'bg-tested',
  Hedge: 'bg-line-strong',
  Outside: 'bg-danger',
}

function AllocationBars({ rows }: { rows: TomReport['allocation'] }) {
  const arrived = useArrived()
  const line = (who: 'tom' | 'yours') => (
    <div className="flex h-9 w-full overflow-hidden rounded-full bg-sunken">
      {rows.map((r) => {
        const share = num(who === 'tom' ? r.tom : r.yours) ?? 0
        if (share <= 0) return null
        return (
          <div
            key={r.key}
            className={`flex items-center justify-center text-[12px] font-semibold text-white ${BUCKET_COLOR[r.key]}`}
            style={{ width: arrived ? `${share * 100}%` : '0%', transition: 'width 900ms var(--ease)' }}
            title={`${r.label}: ${pct(share, 0)}`}
          >
            {share >= 0.08 ? pct(share, 0) : ''}
          </div>
        )
      })}
    </div>
  )
  return (
    <div className={`${card} px-6 py-5`}>
      <div className="grid items-center gap-x-4 gap-y-3 sm:grid-cols-[72px_1fr]">
        <div className="label">Tom</div>
        {line('tom')}
        <div className="label">You</div>
        {line('yours')}
      </div>
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] text-muted">
        {rows.map((r) => (
          <span key={r.key} className="inline-flex items-center gap-1.5">
            <span className={`h-2.5 w-2.5 rounded-full ${BUCKET_COLOR[r.key]}`} />
            {r.label}
          </span>
        ))}
      </div>
      <p className="mt-3 text-[13px] text-faint">
        Yours is the capital each open trade ties up: buying power, or what a debit trade cost if that is more.
      </p>
    </div>
  )
}

/* ---- checklist ----------------------------------------------------------------- */

function CheckRow({ item, index }: { item: TomChecklistItem; index: number }) {
  const t = TONE[item.status]
  return (
    <li className="flex items-start gap-4 py-4">
      <span className="figure w-7 shrink-0 text-[20px] text-faint">{index + 1}</span>
      <div className="min-w-0 flex-1">
        <div className="text-[16px] font-medium">{item.label}</div>
        {item.detail && <div className="mt-0.5 text-[14px] text-muted">{item.detail}</div>}
      </div>
      <span className={`num shrink-0 text-right text-[15px] font-semibold ${t.text}`}>{item.value}</span>
    </li>
  )
}

/* ---- the page ------------------------------------------------------------------ */

const SECTIONS = [
  { id: 'tom-fixes', label: 'Fix first' },
  { id: 'tom-book', label: 'Limits' },
  { id: 'tom-regime', label: 'Regime' },
  { id: 'tom-positions', label: 'Positions' },
  { id: 'tom-history', label: 'History' },
  { id: 'tom-plan', label: 'Your size' },
  { id: 'tom-tonight', label: 'Tonight' },
]

const SECTION_OF: Record<string, string> = {
  positions: 'tom-positions',
  book: 'tom-book',
  history: 'tom-history',
  plan: 'tom-plan',
}

function go(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export function Tom() {
  const { data, error, loading, reload } = useAsync(() => api.tom(), [], 60_000)

  if (error && !data) return <ErrorPanel error={error} onRetry={reload} />
  if (loading && !data) return <Loading label="Holding your book up against Tom's plan" />
  if (!data) return null

  const { score, market } = data
  const facts = data.history_facts
  const perMonth = num(facts.per_month)
  const netLiq = num(data.net_liq)
  const monthlyShare = perMonth !== null && netLiq ? perMonth / netLiq : null
  const months = num(facts.months)
  const positionsBy = (product: string) => data.positions.filter((p) => p.product === product)

  return (
    <div className="space-y-16">
      {/* ---- every rule, first: the page's answer at a glance ------------------ */}
      <section id="tom-rules" className={`${card} scroll-mt-28 px-6 py-5`}>
        <div className="label">Every rule, today</div>
        <ul className="mt-3 grid gap-x-10 gap-y-2.5 md:grid-cols-2 xl:grid-cols-3">
          {score.items.map((item) => (
            <li key={item.label} className="flex items-start justify-between gap-3">
              <span className="text-[14px]">{item.label}</span>
              <Chip status={item.status}>{item.value}</Chip>
            </li>
          ))}
        </ul>
      </section>

      {/* ---- hero ---------------------------------------------------------- */}
      <section className={`${card} relative overflow-hidden px-8 py-10 sm:px-12`}>
        <div
          aria-hidden
          className="pointer-events-none absolute -left-32 -top-40 h-[28rem] w-[28rem] rounded-full opacity-30 blur-3xl"
          style={{ background: 'radial-gradient(closest-side, var(--accent), transparent)' }}
        />
        <div className="relative grid items-center gap-10 md:grid-cols-[1fr_auto]">
          <div className="min-w-0">
            <div className="label text-accent">Tom King · 2026 trading plan</div>
            <h1 className="display mt-3 text-[clamp(30px,4.2vw,50px)] leading-[1.05]">
              Today you keep <span className="gold-text">{score.kept} of {score.judged}</span> of Tom's rules.
            </h1>
            <p className="mt-4 max-w-[62ch] text-[17px] leading-relaxed text-muted">
              Your live book measured against his plan: buying power and Greeks, every open position's size, stop
              and target under his own exits, the chart the way he reads it, and what{' '}
              {months ? `${Math.round(months)} months` : 'the history'} of your trades say about each of his rules.
            </p>
            {market && (
              <button
                onClick={() => go('tom-regime')}
                className="mt-6 inline-flex flex-wrap items-center gap-x-3 gap-y-1 rounded-full border border-line bg-raised px-4 py-2 text-left text-[14px] hover:bg-hover"
              >
                <span className={`h-2 w-2 rounded-full ${REGIME_TONE[market.label].dot}`} />
                <span className="font-semibold">Market {market.label.toLowerCase()}</span>
                <span className="text-muted">
                  {market.bullish} of 4 bullish · Tom focuses on {market.focus.join(', ')}
                </span>
              </button>
            )}
          </div>
          <ScoreRing kept={score.kept} judged={score.judged} />
        </div>
        <nav className="relative mt-10 flex flex-wrap gap-2 border-t border-line pt-5" aria-label="Sections">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              onClick={() => go(s.id)}
              className="rounded-full px-3.5 py-1.5 text-[14px] text-muted hover:bg-hover hover:text-ink"
            >
              {s.label}
            </button>
          ))}
          <span className="ml-auto self-center text-[12px] text-faint">
            Updated {relativeTime(data.as_of)}
          </span>
        </nav>
      </section>

      {/* ---- the three biggest gaps ------------------------------------------ */}
      <section id="tom-fixes" className="scroll-mt-28">
        <SectionHeading title="Fix these first" hint="the three gaps with the most money at stake" />
        {data.fixes.length === 0 ? (
          <Empty title="Nothing stands out. The book is inside Tom's rules." />
        ) : (
          <ol className="stagger grid gap-4 md:grid-cols-3">
            {data.fixes.map((f, i) => (
              <li key={f.title} className={`${card} lift flex flex-col px-6 py-6`}>
                <div className="flex items-center gap-3">
                  <span className="figure flex h-9 w-9 items-center justify-center rounded-full bg-danger-soft text-[18px] text-danger">
                    {i + 1}
                  </span>
                  <span className="label">{f.section === 'history' ? 'Habit' : f.section === 'book' ? 'The book' : f.section === 'plan' ? 'Strategy mix' : 'Position'}</span>
                </div>
                <h3 className="display mt-4 text-[21px] leading-snug">{f.title}</h3>
                <p className="mt-2 flex-1 text-[15px] leading-relaxed text-muted">{f.detail}</p>
                <button onClick={() => go(SECTION_OF[f.section] ?? 'tom-positions')} className="mt-5 self-start text-[14px] font-medium text-accent hover:underline">
                  See the detail ↓
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* ---- the book's dials ------------------------------------------------------ */}
      <section id="tom-book" className="scroll-mt-28">
        <SectionHeading
          title="Your book against Tom's limits"
          help="Plan §2 and §3: buying power is the main measure of risk (40–50% in use), delta within 0.2% of net liq beta-weighted to SPY, theta at least 0.3–0.4% of net liq a day, and vega no more than 1–1.5× theta."
        />
        <div className="stagger grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {data.gauges.map((g) => (
            <GaugeCard key={g.key} g={g} />
          ))}
        </div>
        <Allocation total={data.allocation_total} rows={data.strategy_bp} positions={data.positions} netLiq={num(data.net_liq)} />
      </section>

      {/* ---- the regime ---------------------------------------------------------------- */}
      <section id="tom-regime" className="scroll-mt-28">
        <SectionHeading
          title="The market, the way Tom reads it"
          hint="daily charts · four tests"
          help="Plan §7: price above the 21 EMA, the 8 EMA above the 21, the parabolic SAR under price, RSI above 50. Three or four true is bullish, two is neutral, one or none is bearish. The same four tests run on each product you hold; futures use Yahoo's front-month continuous chart."
        />
        {market ? (
          <div className="grid gap-4 xl:grid-cols-[minmax(380px,5fr)_7fr]">
            {/* Pinned while the product cards beside it scroll. The wrapper
                carries the stickiness: the card's own glass class sets its
                position, and an unlayered rule outranks a utility. */}
            <div className="self-start xl:sticky xl:top-28">
              <MarketCard market={market} />
            </div>
            <div className="stagger grid content-start gap-4 sm:grid-cols-2">
              {data.products.map((r) => (
                <ProductCard key={r.product} regime={r} positions={positionsBy(r.product)} />
              ))}
            </div>
          </div>
        ) : (
          <Empty
            title="No chart for SPY right now."
            hint={Object.keys(data.price_errors).length ? `Price history: ${Object.values(data.price_errors)[0]}` : undefined}
          />
        )}
      </section>

      {/* ---- open positions ---------------------------------------------------------------- */}
      <section id="tom-positions" className="scroll-mt-28">
        <SectionHeading
          title="Open positions through Tom's eyes"
          hint={`${data.positions.length} open · worst first`}
          help="Size is what a position could lose under Tom's own exit for it — 2.5× the credit on a strangle, 3× on a naked put, the max loss on a defined-risk trade — against his cap of 2% of net liq per trade (1.5% on a strangle, 1% on a spec 11x). The stop bar fills as the position approaches that exit; the target bar as it approaches his profit target."
        />
        <PositionsTable positions={data.positions} />
      </section>

      {/* ---- history ---------------------------------------------------------------------------- */}
      <section id="tom-history" className="scroll-mt-28">
        <SectionHeading
          title="What your history says about each rule"
          hint={`${facts.trades} closed trades${facts.first_trade ? ` since ${fullDate(facts.first_trade)}` : ''}`}
        />
        <div className="stagger mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <FactTile label="Realized since the start" value={money(facts.realized, { sign: true, cents: false })} sub={`${num(facts.months)?.toFixed(1) ?? EM_DASH} months, after fees`} tone={(num(facts.realized) ?? 0) >= 0 ? 'profit' : 'loss'} />
          <FactTile
            label="A month, on average"
            value={money(facts.per_month, { sign: true, cents: false })}
            sub={monthlyShare !== null ? `${pct(monthlyShare, 2)} of net liq · Tom aims for 2–3%` : 'Tom aims for 2–3% of net liq'}
            tone={(perMonth ?? 0) >= 0 ? 'profit' : 'loss'}
          />
          <FactTile label="Lost beyond Tom's stops" value={money(facts.loss_beyond_stops, { cents: false })} sub="the part of each loss past his exit" tone="loss" />
          <FactTile label="Fees paid" value={money(facts.fees, { cents: false })} sub={`${pct((num(facts.fees) ?? 0) / Math.max(Math.abs(num(facts.realized) ?? 1), 1), 0)} of what you kept`} tone="muted" />
        </div>
        <div className="stagger grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {data.history.map((r) => (
            <HistoryCard key={r.key} rule={r} />
          ))}
        </div>
      </section>

      {/* ---- Tom's plan at your size ------------------------------------------------------------- */}
      <section id="tom-plan" className="scroll-mt-28">
        <SectionHeading
          title="Tom's plan at your size"
          hint={netLiq ? `net liq ${money(netLiq, { cents: false })}` : undefined}
          help="Tom's 2026 plan is written for a $500,000 account. These are the same percentages applied to yours."
        />
        <div className="stagger grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {data.sizing.map((s) => (
            <div key={s.label} className={`${card} lift px-6 py-5`}>
              <div className="label">{s.label}</div>
              <div className="figure mt-2 text-[26px] leading-tight">{s.value}</div>
              <div className="mt-1 text-[13px] text-muted">{s.note}</div>
            </div>
          ))}
        </div>
        <div className="mt-6">
          <div className="mb-3 text-[15px] text-muted">
            Where the capital sits: Tom runs 30% 11x, 40% Dynamic PMCC and 30% spec trades.
          </div>
          <AllocationBars rows={data.allocation} />
        </div>
      </section>

      {/* ---- tonight -------------------------------------------------------------------------------- */}
      <section id="tom-tonight" className="scroll-mt-28">
        <SectionHeading title="Tonight's review" hint="Tom's nightly routine, with your numbers in it" />
        <div className="grid gap-4 xl:grid-cols-[3fr_2fr]">
          <ol className={`${card} divide-y divide-line px-6`}>
            {data.checklist.map((item, i) => (
              <CheckRow key={item.label} item={item} index={i} />
            ))}
          </ol>
          {data.reduction.length > 0 && (
          <div className={`${card} px-6 py-5`}>
              <div>
                <div className="label">Bringing buying power down, Tom's order</div>
                <ol className="mt-2 space-y-1.5 text-[14px]">
                  {data.reduction.map((r, i) => (
                    <li key={r.step}>
                      <span className="font-medium">
                        {i + 1}. {r.step}
                      </span>
                      {r.positions.length > 0 && <span className="text-muted"> — {r.positions.join(', ')}</span>}
                    </li>
                  ))}
                </ol>
              </div>
          </div>
          )}
        </div>
      </section>

      <footer className="text-[13px] leading-relaxed text-faint">
        Measurements against Tom King's published rules, not advice — what to do about a gap is your call. Sources:{' '}
        {data.sources.join(' · ')}.
        {Object.keys(data.price_errors).length > 0 && (
          <> No chart for {Object.keys(data.price_errors).join(', ')}.</>
        )}
      </footer>
    </div>
  )
}

function FactTile({ label, value, sub, tone }: { label: string; value: string; sub: string; tone: 'profit' | 'loss' | 'muted' }) {
  return (
    <div className={`${card} lift px-6 py-5`}>
      <div className="label">{label}</div>
      <div className={`figure mt-2 text-[30px] leading-none ${tone === 'profit' ? 'text-profit' : tone === 'loss' ? 'text-loss' : 'text-ink'}`}>
        {value}
      </div>
      <div className="mt-2 text-[13px] text-muted">{sub}</div>
    </div>
  )
}

/* Allocation: the shares of the 50% of net liq he allows (§8: 11x
   30%, PMCC 40%, spec 30%), each as money in use against money allowed, and
   the whole book's use against the whole allowance. Each line opens to the
   strategies in it and what each one ties up. */
function Allocation({
  total,
  rows,
  positions,
  netLiq,
}: {
  total: TomReport['allocation_total']
  rows: TomReport['strategy_bp']
  positions: TomPosition[]
  netLiq: number | null
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const toggle = (key: string) =>
    setOpen((was) => {
      const next = new Set(was)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  const used = num(total.used) ?? 0
  const allowed = num(total.allowed)

  const bar = (value: number, cap: number | null, over: boolean, kept = false) => (
    /* Full bar = the limit: how much of what is allowed is in use. Where
       nothing is allowed, any money at all fills it. */
    <div className="relative h-2 w-full min-w-[84px] overflow-hidden rounded-full bg-sunken">
      <div
        className={`absolute inset-y-0 left-0 rounded-full ${over ? 'bg-danger' : kept ? 'bg-tested' : 'bg-accent'}`}
        style={{
          width: `${cap ? Math.min(value / cap, 1) * 100 : value > 0 ? 100 : 0}%`,
          transition: 'width 800ms var(--ease)',
        }}
      />
    </div>
  )
  // Shares of net liq first: "using 9.7% of 20% allowed" reads at a glance;
  // the dollars sit underneath.
  const share = (v: number | null) => (v === null || !netLiq ? EM_DASH : pct(v / netLiq, 1))
  const figures = (value: number, cap: number | null, over: boolean) => (
    <span className={`num whitespace-nowrap text-right ${over ? 'text-danger' : ''}`}>
      <span className="block text-[15px] font-medium">
        Using {share(value)}
        <span className="font-normal text-muted"> · Allowed {share(cap)}</span>
      </span>
      <span className="block text-[12px] text-faint">
        {money(value, { cents: false })} of {cap === null ? EM_DASH : money(cap, { cents: false })}
      </span>
    </span>
  )

  return (
    <div className={`${card} mt-4 px-6 py-5`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <div className="label">Allocation</div>
        <div className="text-[13px] text-muted">
          Buying power as a share of net liq: what you use, and what Tom allows. Click a line to see what is in it.
        </div>
      </div>
      {total.pending && (
        <p className="mt-3 rounded-card border border-tested/40 bg-tested/10 px-4 py-2.5 text-[14px]">
          Still loading buying power from tastytrade. Some lines read too low until it arrives; the page
          refreshes on its own.
        </p>
      )}
      <ul className={`mt-3 space-y-1 ${total.pending ? 'opacity-50' : ''}`}>
        <li className="grid items-center gap-x-4 gap-y-1 px-2 py-2 sm:grid-cols-[minmax(0,260px)_minmax(0,1fr)_auto]">
          <span className="min-w-0">
            <span className="block text-[15px] font-semibold">Total</span>
            {(num(total.paid_in_cash) ?? 0) > 0 && (
              <span className="block text-[12px] text-faint">
                Includes {money(total.paid_in_cash, { cents: false })} paid for long calls, which tastytrade does not
                count as buying power
              </span>
            )}
          </span>
          {bar(used, allowed, allowed !== null && used > allowed)}
          {figures(used, allowed, allowed !== null && used > allowed)}
        </li>
        <li aria-hidden className="border-t border-line" />
        {rows.map((s) => {
          const v = num(s.buying_power) ?? 0
          const cap = num(s.cap)
          const inside = positions
            .filter((p) => p.bucket === s.playbook)
            .sort((a, b) => (num(b.capital) ?? 0) - (num(a.capital) ?? 0))
          const isOpen = open.has(s.playbook)
          return (
            <li key={s.playbook} className="rounded-card">
              <button
                onClick={() => toggle(s.playbook)}
                aria-expanded={isOpen}
                className="grid w-full items-center gap-x-4 gap-y-1 rounded-card px-2 py-2 text-left hover:bg-hover sm:grid-cols-[minmax(0,260px)_minmax(0,1fr)_auto]"
              >
                <span className="min-w-0">
                  <span className="block truncate text-[15px]">
                    <span className="mr-1.5 inline-block w-3 text-muted">{isOpen ? '▾' : '▸'}</span>
                    {s.name} <span className="text-muted">· {inside.length}</span>
                  </span>
                  {s.playbook === 'Outside' && (
                    <span className={`block truncate pl-[18px] text-[12px] ${s.over ? 'text-danger' : 'text-tested'}`}>
                      {s.over ? 'Not in your plan: no buying power allowed' : 'Kept for now on purpose'}
                    </span>
                  )}
                </span>
                {bar(v, cap, s.over, (num(s.excused) ?? 0) > 0)}
                {figures(v, cap, s.over)}
              </button>
              {isOpen && (
                <div className="mb-2 ml-[26px] mr-2 mt-1 overflow-x-auto rounded-card border border-line">
                  {inside.length === 0 ? (
                    <p className="px-4 py-3 text-[14px] text-muted">Nothing open in this part.</p>
                  ) : (
                    <table className="w-full text-[14px]">
                      <thead>
                        <tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-faint">
                          <th className="w-full py-2 pl-4 pr-3 font-medium">Strategy</th>
                          <th className="py-2 pr-3 text-right font-medium" title="Buying power the broker holds for it">
                            Buying power
                          </th>
                          <th
                            className="py-2 pr-3 text-right font-medium"
                            title="What it ties up: its buying power, or what it cost if that is more (a LEAP)"
                          >
                            Counts as
                          </th>
                          <th className="py-2 pr-3 text-right font-medium">Of this limit</th>
                          <th className="py-2 pr-4 text-right font-medium">Of net liq</th>
                        </tr>
                      </thead>
                      <tbody className="num">
                        {inside.map((p) => {
                          const c = num(p.capital) ?? 0
                          const bp = num(p.buying_power)
                          return (
                            <tr key={p.id} className="border-b border-line/60 last:border-0">
                              <td className="py-2 pl-4 pr-3 font-sans">
                                <span className="font-medium">{p.title}</span>{' '}
                                <span className="text-muted">· {p.playbook_name}</span>
                              </td>
                              <td className="whitespace-nowrap py-2 pr-3 text-right text-muted">
                                {bp === null ? EM_DASH : money(bp, { cents: false })}
                              </td>
                              <td className="whitespace-nowrap py-2 pr-3 text-right">{money(c, { cents: false })}</td>
                              <td className="whitespace-nowrap py-2 pr-3 text-right text-muted">
                                {cap ? pct(c / cap, 0) : EM_DASH}
                              </td>
                              <td className="whitespace-nowrap py-2 pr-4 text-right text-muted">
                                {netLiq ? pct(c / netLiq, 1) : EM_DASH}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
