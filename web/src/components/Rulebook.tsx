import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import { shortDate } from '../lib/format'
import { ErrorPanel, SectionHeading } from './States'
import type { RuleSection, RuleSetFields, RulebookView } from '../types'

/* His rulebook: one rule set per kind of strategy, read in from his own
   documents, and which of his named strategies each one covers. A strategy
   is matched by the name he gave it; one whose name could mean two rule sets,
   or none, waits here for his call rather than being guessed. */

const box = 'sheened rounded-card border border-line bg-raised shadow-[var(--shadow-sm)]'

const SECTION_TITLES: Record<RuleSection, string> = {
  entry: 'Entry',
  size: 'Size',
  profit: 'Take profit',
  loss: 'Cut the loss',
  time: 'Close or roll',
  adjust: 'Adjust',
  avoid: 'Never',
  other: 'Also',
}
const ORDER: RuleSection[] = ['entry', 'size', 'profit', 'loss', 'time', 'adjust', 'avoid', 'other']

const pctText = (v: string) => `${Math.round(Number(v) * 1000) / 10}%`

/* The numbers the app watches for this rule set, short. */
function lines(s: RuleSetFields): string[] {
  return [
    s.entry_dte && `Open at ${s.entry_dte[0] === s.entry_dte[1] ? s.entry_dte[0] : `${s.entry_dte[0]}–${s.entry_dte[1]}`} DTE`,
    s.take_profit && `Profit at ${pctText(s.take_profit)}`,
    s.stop_multiple && `Stop at ${Number(s.stop_multiple)}× credit`,
    s.stop_of_debit && `Stop at ${pctText(s.stop_of_debit)} of the long side`,
    s.dte_exit != null && `Out at ${s.dte_exit} DTE`,
    s.adjust_delta && `Adjust at ${Math.round(Number(s.adjust_delta) * 100)}Δ`,
    s.max_loss && `Loss ≤ ${pctText(s.max_loss)} of net liq`,
    s.max_bp && `BP ≤ ${pctText(s.max_bp)} of allowed BP`,
    s.max_credit && `Credit ≤ ${pctText(s.max_credit)} of net liq`,
  ].filter((x): x is string => Boolean(x))
}

/* Every rule of a set, in the words of his document, grouped by when it applies. */
export function RuleSetRules({ set }: { set: RuleSetFields }) {
  const groups = ORDER.map((sec) => ({ sec, rules: set.rules.filter((r) => r.section === sec) })).filter(
    (g) => g.rules.length > 0,
  )
  if (groups.length === 0) return null
  return (
    <dl className="space-y-2">
      {groups.map((g) => (
        <div key={g.sec}>
          <dt className="text-caption uppercase tracking-wider text-faint">{SECTION_TITLES[g.sec]}</dt>
          {g.rules.map((r, i) => (
            <dd key={i} className="text-body leading-snug text-ink">
              {r.text}
            </dd>
          ))}
        </div>
      ))}
    </dl>
  )
}

export function SourceLink({ set }: { set: RuleSetFields }) {
  if (!set.source && !set.source_url) return null
  const label = set.source || 'source'
  return set.source_url ? (
    <a href={set.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
      {label}
    </a>
  ) : (
    <span>{label}</span>
  )
}

type Row = RulebookView['strategies'][number]

/* ---- adding and editing a rule set ------------------------------------------ */

const SECTION_HINTS: Record<RuleSection, string> = {
  entry: 'When you open it: the chart, IV, the calendar',
  size: 'How big one trade may be',
  profit: 'When you take the win',
  loss: 'When you take the loss',
  time: 'When you close or roll on time',
  adjust: 'What you change, and when',
  avoid: 'What you never do with it',
  other: 'Anything else',
}

/* The form keeps what he types, in the units he thinks in: 50 for 50%, 30 for
   a 30-delta. The numbers go back to the server the way it reads them. */
type Draft = {
  id: string | null
  name: string
  aliases: string
  source: string
  source_url: string
  entry_from: string
  entry_to: string
  take_profit: string
  stop_multiple: string
  stop_of_debit: string
  dte_exit: string
  adjust_delta: string
  max_loss: string
  max_bp: string
  max_credit: string
  rules: Record<RuleSection, string>
}

const asPct = (v: string | null) => (v == null ? '' : String(Math.round(Number(v) * 1000) / 10))
const asDelta = (v: string | null) => (v == null ? '' : String(Math.round(Number(v) * 100)))

function draftOf(set: RuleSetFields | null): Draft {
  const rules = Object.fromEntries(ORDER.map((sec) => [sec, ''])) as Record<RuleSection, string>
  for (const r of set?.rules ?? []) rules[r.section] = rules[r.section] ? `${rules[r.section]}\n${r.text}` : r.text
  return {
    id: set?.id ?? null,
    name: set?.name ?? '',
    aliases: (set?.aliases ?? []).join(', '),
    source: set?.source ?? '',
    source_url: set?.source_url ?? '',
    entry_from: set?.entry_dte ? String(set.entry_dte[0]) : '',
    entry_to: set?.entry_dte ? String(set.entry_dte[1]) : '',
    take_profit: asPct(set?.take_profit ?? null),
    stop_multiple: set?.stop_multiple == null ? '' : String(Number(set.stop_multiple)),
    stop_of_debit: asPct(set?.stop_of_debit ?? null),
    dte_exit: set?.dte_exit == null ? '' : String(set.dte_exit),
    adjust_delta: asDelta(set?.adjust_delta ?? null),
    max_loss: asPct(set?.max_loss ?? null),
    max_bp: asPct(set?.max_bp ?? null),
    max_credit: asPct(set?.max_credit ?? null),
    rules,
  }
}

/* The draft as the server reads a rule set. */
function payloadOf(d: Draft): Record<string, unknown> {
  const t = (v: string) => v.trim()
  const pctOf = (v: string) => (t(v) ? `${t(v)}%` : null)
  const from = t(d.entry_from) || t(d.entry_to)
  const to = t(d.entry_to) || t(d.entry_from)
  return {
    ...(d.id ? { id: d.id } : {}),
    name: t(d.name),
    aliases: d.aliases.split(',').map((a) => a.trim()).filter(Boolean),
    source: t(d.source),
    source_url: t(d.source_url),
    entry_dte: from ? [Number(from), Number(to)] : null,
    take_profit: pctOf(d.take_profit),
    stop_multiple: t(d.stop_multiple) || null,
    stop_of_debit: pctOf(d.stop_of_debit),
    dte_exit: t(d.dte_exit) ? Number(t(d.dte_exit)) : null,
    adjust_delta: t(d.adjust_delta) ? String(Number(t(d.adjust_delta)) / 100) : null,
    max_loss: pctOf(d.max_loss),
    max_bp: pctOf(d.max_bp),
    max_credit: pctOf(d.max_credit),
    rules: ORDER.flatMap((sec) =>
      d.rules[sec]
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((text) => ({ section: sec, text })),
    ),
  }
}

const input =
  'num w-full rounded-sm border border-line bg-sunken px-2 py-1.5 text-body text-ink placeholder:text-faint focus:border-accent focus:outline-none'

function Field({ label, unit, value, onChange, wide }: {
  label: string
  unit?: string
  value: string
  onChange: (v: string) => void
  wide?: boolean
}) {
  return (
    <label className={`block ${wide ? 'sm:col-span-2' : ''}`}>
      <span className="text-note text-muted">{label}</span>
      <span className="mt-1 flex items-center gap-2">
        <input value={value} onChange={(e) => onChange(e.target.value)} className={input} />
        {unit && <span className="shrink-0 text-note text-faint">{unit}</span>}
      </span>
    </label>
  )
}

function RuleSetEditor({
  start,
  sets,
  onSave,
  onCancel,
  onDelete,
}: {
  start: RuleSetFields | null
  sets: RuleSetFields[]
  onSave: (d: Draft) => Promise<void>
  onCancel: () => void
  onDelete?: () => Promise<void>
}) {
  const [d, setD] = useState<Draft>(() => draftOf(start))
  const [busy, setBusy] = useState(false)
  const [sure, setSure] = useState(false)
  const set = (k: keyof Draft) => (v: string) => setD((was) => ({ ...was, [k]: v }))
  const isNew = start === null

  async function save() {
    setBusy(true)
    try {
      await onSave(d)
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className={`${box} space-y-4 p-4 sm:p-5`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-lead font-semibold">{isNew ? 'A new strategy for your plan' : `Edit ${start.name}`}</h3>
        {isNew && sets.length > 0 && (
          <select
            defaultValue=""
            onChange={(e) => {
              const from = sets.find((x) => x.id === e.target.value)
              if (from) setD({ ...draftOf(from), id: null, name: `${from.name} (copy)` })
            }}
            className="rounded-sm border border-line bg-raised px-2 py-1 text-body"
            aria-label="Start from"
          >
            <option value="">Start blank</option>
            {sets.map((x) => (
              <option key={x.id} value={x.id}>
                Copy of {x.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Name" value={d.name} onChange={set('name')} />
        <Field label="Other names you use for it (comma between)" value={d.aliases} onChange={set('aliases')} />
      </div>

      <div>
        <div className="label">The numbers the app watches</div>
        <p className="mt-0.5 text-note text-faint">Leave a box empty when your plan has no number for it.</p>
        <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Open from" unit="DTE" value={d.entry_from} onChange={set('entry_from')} />
          <Field label="to" unit="DTE" value={d.entry_to} onChange={set('entry_to')} />
          <Field label="Take profit at" unit="% of max" value={d.take_profit} onChange={set('take_profit')} />
          <Field label="Stop at" unit="× credit" value={d.stop_multiple} onChange={set('stop_multiple')} />
          <Field label="Or stop at" unit="% of long cost" value={d.stop_of_debit} onChange={set('stop_of_debit')} />
          <Field label="Close or roll at" unit="DTE" value={d.dte_exit} onChange={set('dte_exit')} />
          <Field label="Adjust at short" unit="Δ" value={d.adjust_delta} onChange={set('adjust_delta')} />
          <Field label="Max loss" unit="% net liq" value={d.max_loss} onChange={set('max_loss')} />
          <Field label="Max buying power" unit="% of allowed" value={d.max_bp} onChange={set('max_bp')} />
          <Field label="Max credit" unit="% net liq" value={d.max_credit} onChange={set('max_credit')} />
        </div>
      </div>

      <div>
        <div className="label">The rules in your words</div>
        <p className="mt-0.5 text-note text-faint">One rule a line.</p>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {ORDER.map((sec) => (
            <label key={sec} className="block">
              <span className="text-note text-muted">
                {SECTION_TITLES[sec]} <span className="text-faint">· {SECTION_HINTS[sec]}</span>
              </span>
              <textarea
                rows={Math.max(2, d.rules[sec].split('\n').length)}
                value={d.rules[sec]}
                onChange={(e) => setD((was) => ({ ...was, rules: { ...was.rules, [sec]: e.target.value } }))}
                className={`${input.replace('num ', '')} mt-1 resize-y leading-snug`}
              />
            </label>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Where it comes from (optional)" value={d.source} onChange={set('source')} />
        <Field label="Link to it (optional)" value={d.source_url} onChange={set('source_url')} />
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <button
          onClick={save}
          disabled={busy || !d.name.trim()}
          className="rounded-sm bg-accent px-4 py-1.5 text-body font-medium text-white disabled:opacity-50"
        >
          {busy ? 'Saving…' : isNew ? 'Add to my plan' : 'Save'}
        </button>
        <button onClick={onCancel} className="rounded-sm border border-line px-4 py-1.5 text-body text-muted hover:text-ink">
          Cancel
        </button>
        {onDelete && (
          <button
            onClick={async () => (sure ? onDelete() : setSure(true))}
            className={`ml-auto rounded-sm px-3 py-1.5 text-body ${sure ? 'bg-danger text-white' : 'text-loss hover:bg-danger-soft'}`}
          >
            {sure ? 'Yes, delete it' : 'Delete this strategy'}
          </button>
        )}
      </div>
    </article>
  )
}

/* Which current strategies follow this set, and a way to add or take one off. */
function Links({ set, rows, onPick }: { set: RuleSetFields; rows: Row[]; onPick: (namedId: string, rulesId: string) => void }) {
  const users = rows.filter((r) => r.rules_id === set.id && r.current)
  const others = rows.filter((r) => r.rules_id !== set.id && r.current)
  return (
    <div className="mt-auto space-y-2 border-t border-line pt-3">
      <div className="text-note text-muted">{users.length === 0 ? 'No strategy follows it yet' : 'Followed by'}</div>
      {users.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {users.map((u) => (
            <span key={u.named_id} className="inline-flex items-center gap-1 rounded-full bg-accent-soft py-0.5 pl-2.5 pr-1 text-note text-accent">
              {u.name} <span className="text-accent/70">{u.product}</span>
              <button
                onClick={() => onPick(u.named_id, 'none')}
                title={`${u.name} stops following these rules`}
                aria-label={`Unlink ${u.name}`}
                className="rounded-full px-1.5 leading-none hover:bg-accent/20"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      {others.length > 0 && (
        <select
          value=""
          onChange={(e) => e.target.value && onPick(e.target.value, set.id)}
          className="w-full rounded-sm border border-dashed border-line bg-raised px-2 py-1 text-body text-muted"
          aria-label={`Link a strategy to ${set.name}`}
        >
          <option value="">+ Link one of your strategies</option>
          {others.map((r) => (
            <option key={r.named_id} value={r.named_id}>
              {r.name} · {r.product}
              {r.rules_name ? ` (now ${r.rules_name})` : ''}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

function Picker({ row, sets, onPick }: { row: Row; sets: RuleSetFields[]; onPick: (id: string) => void }) {
  const value = row.status === 'chosen' ? (row.rules_id ?? 'auto') : row.status === 'none' ? 'none' : 'auto'
  return (
    <select
      value={value}
      onChange={(e) => onPick(e.target.value)}
      className="max-w-full rounded-sm border border-line bg-raised px-2 py-1 text-body"
      aria-label={`Rule set for ${row.name}`}
    >
      <option value="auto">{row.status === 'matched' ? `From its name: ${row.rules_name}` : 'Read from its name'}</option>
      {sets.map((s) => (
        <option key={s.id} value={s.id}>
          {s.name}
        </option>
      ))}
      <option value="none">No rules for this one</option>
    </select>
  )
}

function Ask({ row, sets, onPick }: { row: Row; sets: RuleSetFields[]; onPick: (id: string) => void }) {
  const likely = row.candidates.filter((c) => c.score >= 0.5).slice(0, 2)
  return (
    <li className="rounded-card border border-tested/40 bg-tested-soft px-3.5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-copy font-semibold text-ink">{row.name}</span>
        <span className="text-body text-muted">{row.product}</span>
        <span className="text-note text-faint">{row.shape}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {likely.map((c) => (
          <button
            key={c.rules_id}
            onClick={() => onPick(c.rules_id)}
            className="rounded-full border border-accent/50 bg-accent-soft px-3 py-1 text-body font-medium text-accent"
          >
            {c.name}
          </button>
        ))}
        <Picker row={row} sets={sets} onPick={onPick} />
      </div>
    </li>
  )
}

export function RulebookSection() {
  const { data, error, loading, reload } = useAsync(() => api.rulebook(), [])
  const [view, setView] = useState<RulebookView | null>(null)
  const [older, setOlder] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  // Which set is open in the form: its id, 'new' for a new one, or none.
  const [editing, setEditing] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  const shown = view ?? data
  if (loading && !shown) return null
  if (!shown) return null

  const book = shown.rulebook

  async function saveSets(sets: unknown[]) {
    setSaveError(null)
    try {
      setView(await api.saveRulebook({ source: book?.source ?? '', sets }))
      setEditing(null)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e))
    }
  }
  // Every other set goes back exactly as it came; only the one edited changes.
  const others = (id: string | null) => (book?.sets ?? []).filter((x) => x.id !== id)

  if (!book || (book.sets.length === 0 && editing === null)) {
    return (
      <section>
        <SectionHeading title="Your rulebook" />
        {editing === 'new' ? (
          <RuleSetEditor start={null} sets={[]} onSave={(d) => saveSets([payloadOf(d)])} onCancel={() => setEditing(null)} />
        ) : (
          <div className={`${box} px-4 py-3.5 text-copy text-muted`}>
            No rulebook yet. Each kind of strategy you trade gets its own rule set, and every strategy
            you name follows one.{' '}
            <button onClick={() => setEditing('new')} className="font-medium text-accent hover:underline">
              Add your first strategy
            </button>
          </div>
        )}
      </section>
    )
  }

  async function pick(namedId: string, rulesId: string) {
    setSaveError(null)
    try {
      setView(await api.chooseRules(namedId, rulesId))
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e))
    }
  }

  const asks = shown.strategies.filter((r) => r.status === 'ask' && r.current)
  const rows = shown.strategies.filter((r) => (older || r.current) && !(r.status === 'ask' && r.current))
  const olderCount = shown.strategies.filter((r) => !r.current).length

  return (
    <section className="space-y-4">
      <SectionHeading
        title="Your rulebook"
        hint={[
          `${book.sets.length} rule set${book.sets.length === 1 ? '' : 's'}`,
          book.source && `from ${book.source}`,
          book.saved_at && `saved ${shortDate(book.saved_at)}`,
        ]
          .filter(Boolean)
          .join(' · ')}
        help="Each strategy you name follows one rule set, read from its name. Where you have not written a plan for a strategy, its rule set is its plan; the size lines are checked either way."
        right={
          editing !== 'new' && (
            <button
              onClick={() => setEditing('new')}
              className="rounded-sm bg-accent px-3 py-1 text-body font-medium text-white"
            >
              + Add a strategy
            </button>
          )
        }
      />

      {editing === 'new' && (
        <RuleSetEditor
          start={null}
          sets={book.sets}
          onSave={(d) => saveSets([...book.sets, payloadOf(d)])}
          onCancel={() => setEditing(null)}
        />
      )}

      {saveError && <p className="text-body text-loss">{saveError}</p>}

      {asks.length > 0 && (
        <div className={`${box} px-4 py-3.5`}>
          <h3 className="text-copy font-semibold">Which rules do these follow?</h3>
          <p className="mt-0.5 text-body text-muted">Their names could mean more than one rule set, or none.</p>
          <ul className="mt-3 space-y-2">
            {asks.map((r) => (
              <Ask key={r.named_id} row={r} sets={book.sets} onPick={(id) => pick(r.named_id, id)} />
            ))}
          </ul>
        </div>
      )}

      {rows.length > 0 && (
        <button
          onClick={() => setShowAll(!showAll)}
          className="text-body text-muted hover:text-ink"
        >
          {showAll ? '▾' : '▸'} Every strategy and the rules it follows
        </button>
      )}
      {rows.length > 0 && showAll && (
        <div className={`${box} overflow-hidden`}>
          <table className="w-full text-body">
            <thead>
              <tr className="border-b border-line text-left text-caption uppercase tracking-wider text-faint">
                <th className="px-4 py-2 font-medium">Strategy</th>
                <th className="px-3 py-2 font-medium">Follows</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.named_id} className="border-b border-line last:border-0">
                  <td className="w-full px-4 py-2">
                    <span className="font-medium text-ink">{r.name}</span>{' '}
                    <span className="text-muted">{r.product}</span>
                    {r.own_plan && <span className="ml-2 text-note text-faint">· your own plan comes first</span>}
                  </td>
                  <td className="px-3 py-2">
                    <Picker row={r} sets={book.sets} onPick={(id) => pick(r.named_id, id)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {olderCount > 0 && (
            <button
              onClick={() => setOlder(!older)}
              className="w-full border-t border-line px-4 py-2 text-left text-note text-muted hover:bg-hover hover:text-ink"
            >
              {older ? 'Hide' : 'Show'} {olderCount} older strateg{olderCount === 1 ? 'y' : 'ies'}
            </button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {book.sets.map((s) => {
          if (editing === s.id) {
            return (
              <div key={s.id} className="md:col-span-2 xl:col-span-3">
                <RuleSetEditor
                  start={s}
                  sets={book.sets}
                  onSave={(d) => saveSets(book.sets.map((x) => (x.id === s.id ? payloadOf(d) : x)))}
                  onCancel={() => setEditing(null)}
                  onDelete={() => saveSets(others(s.id))}
                />
              </div>
            )
          }
          return (
            <article key={s.id} className={`${box} flex flex-col gap-3 p-4`}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-lead font-semibold">{s.name}</h3>
                  <div className="text-note text-muted">
                    <SourceLink set={s} />
                  </div>
                </div>
                <button
                  onClick={() => setEditing(s.id)}
                  className="shrink-0 rounded-sm border border-line px-2.5 py-1 text-note text-muted hover:bg-hover hover:text-ink"
                >
                  Edit
                </button>
              </div>
              {lines(s).length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {lines(s).map((l) => (
                    <span key={l} className="num rounded-full bg-sunken px-2.5 py-0.5 text-note text-ink">
                      {l}
                    </span>
                  ))}
                </div>
              )}
              <RuleSetRules set={s} />
              <Links set={s} rows={shown.strategies} onPick={pick} />
            </article>
          )
        })}
      </div>
    </section>
  )
}
