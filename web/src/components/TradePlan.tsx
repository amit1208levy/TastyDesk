import { useState } from 'react'
import { api } from '../lib/api'
import { shortDate } from '../lib/format'
import type { NamedStrategy, PlanCheck, PlanDetail, TradePlanFields } from '../types'

/* The plan a strategy is traded under: written before the trade, in his own
   words, with one number per promise that the app can watch. Kept as
   versions — a plan rewritten while the trade is on is allowed, and is on the
   record with its date. */

const KEYS = ['profit', 'loss', 'time', 'adjust'] as const
type Key = (typeof KEYS)[number]

const TITLES: Record<Key, string> = {
  profit: 'Take profit',
  loss: 'Cut the loss',
  time: 'Close or roll',
  adjust: 'Adjust',
}

/* Where each promise stands now, for one position. A line that has been
   reached turns red and says what he promised to do. */
export function PlanLines({ checks }: { checks: PlanCheck[] }) {
  if (!checks.length) return null
  return (
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {checks.map((c) => {
        const near = !c.hit && (c.progress ?? 0) >= 0.8
        const tone = c.hit ? 'text-loss' : near ? 'text-tested' : 'text-ink'
        const bar = c.hit ? 'bg-loss' : near ? 'bg-tested' : 'bg-accent'
        return (
          <div
            key={c.key}
            className={`rounded-card border px-3 py-2.5 ${
              c.hit ? 'border-loss/50 bg-loss-soft' : near ? 'border-tested/40 bg-tested-soft' : 'border-line bg-raised'
            }`}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[13px] uppercase tracking-wider text-muted">{c.label}</span>
              <span className={`text-[13px] font-medium ${tone}`}>
                {c.hit ? 'Now' : near ? 'Close' : 'Not yet'}
              </span>
            </div>
            <div className="mt-0.5 text-[15px] text-ink">{c.line}</div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-sunken">
              <div className={`h-full ${bar}`} style={{ width: `${(c.progress ?? 0) * 100}%` }} />
            </div>
            <div className={`mt-1 text-[13px] ${c.reading ? 'text-muted' : 'text-faint'}`}>
              {c.reading ?? 'can’t be measured on this position right now'}
            </div>
            {c.hit && c.note && (
              <div className="mt-1.5 text-[14px] font-medium text-loss">Your plan: {c.note}</div>
            )}
          </div>
        )
      })}
    </div>
  )
}

type Draft = Record<
  'take_profit' | 'stop_multiple' | 'dte_exit' | 'adjust_delta' | `${Key}_note` | 'notes',
  string
>

// The form speaks in the units he thinks in — 50 (%), 2 (×), 21 (days), 30
// (delta) — and the server stores fractions.
function toDraft(p: TradePlanFields | null): Draft {
  const pctOf = (v: string | null) => (v === null ? '' : String(Math.round(Number(v) * 100)))
  return {
    take_profit: pctOf(p?.take_profit ?? null),
    stop_multiple: p?.stop_multiple ?? '',
    dte_exit: p?.dte_exit == null ? '' : String(p.dte_exit),
    adjust_delta: pctOf(p?.adjust_delta ?? null),
    profit_note: p?.profit_note ?? '',
    loss_note: p?.loss_note ?? '',
    time_note: p?.time_note ?? '',
    adjust_note: p?.adjust_note ?? '',
    notes: p?.notes ?? '',
  }
}

function fromDraft(d: Draft): Partial<TradePlanFields> {
  const frac = (v: string) => (v.trim() === '' ? null : String(Number(v) / 100))
  return {
    take_profit: frac(d.take_profit),
    stop_multiple: d.stop_multiple.trim() === '' ? null : d.stop_multiple.trim(),
    dte_exit: d.dte_exit.trim() === '' ? null : Number(d.dte_exit),
    adjust_delta: frac(d.adjust_delta),
    profit_note: d.profit_note,
    loss_note: d.loss_note,
    time_note: d.time_note,
    adjust_note: d.adjust_note,
    notes: d.notes,
  }
}

const ROWS: { key: Key; field: keyof Draft; before: string; after: string; placeholder: string; example: string }[] = [
  {
    key: 'profit',
    field: 'take_profit',
    before: 'I take profit at',
    after: '% of max profit',
    placeholder: '50',
    example: 'e.g. Buy it back, and sell the next month the same day.',
  },
  {
    key: 'loss',
    field: 'stop_multiple',
    before: 'I cut the loss when it reaches',
    after: '× the credit I took in',
    placeholder: '2',
    example: 'e.g. Close the whole thing. No rolling for a debit.',
  },
  {
    key: 'time',
    field: 'dte_exit',
    before: 'With',
    after: 'days left, I close or roll',
    placeholder: '21',
    example: 'e.g. Roll out to the next month for a credit, same strikes.',
  },
  {
    key: 'adjust',
    field: 'adjust_delta',
    before: 'When a short strike reaches',
    after: 'delta, I adjust',
    placeholder: '30',
    example: 'e.g. Roll the untested side closer, to 16 delta. Never invert.',
  },
]

function PlanEditor({
  strategy,
  onSaved,
  onCancel,
}: {
  strategy: NamedStrategy
  onSaved: (s: NamedStrategy) => void
  onCancel?: () => void
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(strategy.plan.current))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const open = strategy.live.count > 0
  const set = (k: keyof Draft) => (e: { target: { value: string } }) =>
    setDraft((d) => ({ ...d, [k]: e.target.value }))

  async function save() {
    setBusy(true)
    setError(null)
    try {
      onSaved(await api.savePlan(strategy.id, fromDraft(draft)))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      {ROWS.map((r) => (
        <div key={r.key} className="rounded-card border border-line bg-sunken p-3.5">
          <div className="text-[13px] uppercase tracking-wider text-muted">{TITLES[r.key]}</div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[16px]">
            <span>{r.before}</span>
            <input
              value={draft[r.field]}
              onChange={set(r.field)}
              inputMode="decimal"
              placeholder={r.placeholder}
              className="num w-20 rounded-sm border border-line bg-raised px-2 py-1 text-right text-[16px]"
            />
            <span>{r.after}</span>
          </div>
          <textarea
            value={draft[`${r.key}_note`]}
            onChange={set(`${r.key}_note`)}
            rows={2}
            placeholder={`What I will do then — ${r.example}`}
            className="mt-2 w-full resize-y rounded-sm border border-line bg-raised px-2.5 py-2 text-[15px] placeholder:text-faint"
          />
        </div>
      ))}
      <div className="rounded-card border border-line bg-sunken p-3.5">
        <div className="text-[13px] uppercase tracking-wider text-muted">Anything else</div>
        <textarea
          value={draft.notes}
          onChange={set('notes')}
          rows={3}
          placeholder="When I enter, how big, what I will not do. e.g. Only sell when IV rank is over 30. Never more than 2 contracts."
          className="mt-2 w-full resize-y rounded-sm border border-line bg-raised px-2.5 py-2 text-[15px] placeholder:text-faint"
        />
      </div>

      {open && strategy.plan.current && (
        <p className="rounded-card border border-tested/40 bg-tested-soft px-3 py-2 text-[14px] text-tested">
          A trade is open. This change is kept with today’s date and marked as made while the
          trade was on — the old plan stays on the record.
        </p>
      )}
      {error && <p className="text-[14px] text-loss">{error}</p>}
      <div className="flex gap-2">
        <button
          onClick={save}
          disabled={busy}
          className="rounded-sm border border-accent/50 bg-accent-soft px-4 py-1.5 text-[15px] font-medium text-accent disabled:opacity-40"
        >
          {busy ? 'Saving…' : strategy.plan.current ? 'Save new version' : 'Make this my plan'}
        </button>
        {onCancel && (
          <button
            onClick={onCancel}
            className="rounded-sm border border-line px-4 py-1.5 text-[15px] text-muted hover:bg-hover hover:text-ink"
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  )
}

function Record({ plan }: { plan: PlanDetail }) {
  const r = plan.record
  if (!r) return null
  return (
    <div>
      <div className="text-[13px] uppercase tracking-wider text-muted">
        Kept or broken · {r.trades} trade{r.trades === 1 ? '' : 's'} closed since{' '}
        {plan.since ? shortDate(plan.since) : 'the plan'}
      </div>
      {r.trades === 0 ? (
        <p className="mt-1 text-[15px] text-muted">
          Nothing has closed under this plan yet. Each trade that closes is scored here against
          its lines.
        </p>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          {r.lines.map((l) => (
            <div key={l.key} className="rounded-card border border-line bg-raised px-3 py-2 text-[15px]">
              <span className="text-muted">{TITLES[l.key]}: </span>
              <span className="text-profit">{l.kept} kept</span>
              {' · '}
              <span className={l.broken ? 'font-medium text-loss' : 'text-faint'}>{l.broken} broken</span>
            </div>
          ))}
        </div>
      )}
      {(plan.changed_while_open ?? 0) > 0 && (
        <p className="mt-2 text-[14px] text-tested">
          Changed {plan.changed_while_open} time{plan.changed_while_open === 1 ? '' : 's'} while a
          trade was open.
        </p>
      )}
    </div>
  )
}

/* The Plan tab of a strategy card. */
export function PlanPanel({
  strategy,
  onChange,
}: {
  strategy: NamedStrategy
  onChange: () => void
}) {
  const plan = strategy.plan
  const [editing, setEditing] = useState(plan.current === null)
  const [showHistory, setShowHistory] = useState(false)

  if (editing) {
    return (
      <div className="mt-4 space-y-3">
        {plan.current === null && (
          <p className="text-[16px] text-ink">
            Write down, before the trade, exactly when you take profit, when you cut the loss and
            how you manage it. Fill in the numbers you want watched — the app flags each one the
            moment it is reached and quotes your own words back to you.
          </p>
        )}
        <PlanEditor
          strategy={strategy}
          onSaved={() => {
            setEditing(false)
            onChange()
          }}
          onCancel={plan.current ? () => setEditing(false) : undefined}
        />
      </div>
    )
  }

  return (
    <div className="mt-4 space-y-5">
      <div>
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-[13px] uppercase tracking-wider text-muted">
            My plan · since {plan.since ? shortDate(plan.since) : '—'}
          </span>
          <button
            onClick={() => setEditing(true)}
            className="rounded-sm border border-line px-3 py-1 text-[14px] text-muted hover:bg-hover hover:text-ink"
          >
            Change plan
          </button>
        </div>
        <ol className="mt-2 space-y-2">
          {plan.sentences.map((s) => (
            <li key={s.key} className="rounded-card border-l-4 border-accent bg-sunken px-4 py-2.5 text-[17px] leading-snug text-ink">
              {s.text}
            </li>
          ))}
        </ol>
      </div>

      <Record plan={plan} />

      {plan.versions.length > 1 && (
        <div>
          <button
            onClick={() => setShowHistory(!showHistory)}
            className="text-[14px] text-muted hover:text-ink"
          >
            {showHistory ? '▴' : '▾'} {plan.versions.length} versions
          </button>
          {showHistory && (
            <ul className="mt-2 space-y-1.5 text-[14px]">
              {[...plan.versions].reverse().map((v, i) => (
                <li key={v.saved_at} className="flex flex-wrap gap-x-3 text-muted">
                  <span className="num text-ink">{shortDate(v.saved_at)}</span>
                  <span>
                    {i === 0 ? 'current' : 'replaced'}
                    {v.while_open && <span className="ml-2 text-tested">changed while a trade was open</span>}
                  </span>
                  <span className="text-faint">
                    {[
                      v.plan.take_profit && `profit ${Math.round(Number(v.plan.take_profit) * 100)}%`,
                      v.plan.stop_multiple && `stop ${v.plan.stop_multiple}×`,
                      v.plan.dte_exit != null && `${v.plan.dte_exit} days`,
                      v.plan.adjust_delta && `adjust ${Math.round(Number(v.plan.adjust_delta) * 100)}Δ`,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
