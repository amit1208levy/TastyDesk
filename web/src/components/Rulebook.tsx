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
          <dt className="text-[12px] uppercase tracking-wider text-faint">{SECTION_TITLES[g.sec]}</dt>
          {g.rules.map((r, i) => (
            <dd key={i} className="text-[14px] leading-snug text-ink">
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

function Picker({ row, sets, onPick }: { row: Row; sets: RuleSetFields[]; onPick: (id: string) => void }) {
  const value = row.status === 'chosen' ? (row.rules_id ?? 'auto') : row.status === 'none' ? 'none' : 'auto'
  return (
    <select
      value={value}
      onChange={(e) => onPick(e.target.value)}
      className="max-w-full rounded-sm border border-line bg-raised px-2 py-1 text-[14px]"
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
        <span className="text-[16px] font-semibold text-ink">{row.name}</span>
        <span className="text-[14px] text-muted">{row.product}</span>
        <span className="text-[13px] text-faint">{row.shape}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {likely.map((c) => (
          <button
            key={c.rules_id}
            onClick={() => onPick(c.rules_id)}
            className="rounded-full border border-accent/50 bg-accent-soft px-3 py-1 text-[14px] font-medium text-accent"
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

  if (error) return <ErrorPanel error={error} onRetry={reload} />
  const shown = view ?? data
  if (loading && !shown) return null
  if (!shown) return null

  const book = shown.rulebook
  if (!book) {
    return (
      <section>
        <SectionHeading title="Your rulebook" />
        <div className={`${box} px-4 py-3.5 text-[15px] text-muted`}>
          No rulebook yet. Send Claude the link to your rules: each kind of strategy gets its own rule
          set, every strategy you name is matched to one by its name, and the ones it can’t tell are
          asked about here.
        </div>
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
  const usedBy = (id: string) => shown.strategies.filter((r) => r.rules_id === id && r.current)

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
      />

      {saveError && <p className="text-[14px] text-loss">{saveError}</p>}

      {asks.length > 0 && (
        <div className={`${box} px-4 py-3.5`}>
          <h3 className="text-[16px] font-semibold">Which rules do these follow?</h3>
          <p className="mt-0.5 text-[14px] text-muted">Their names could mean more than one rule set, or none.</p>
          <ul className="mt-3 space-y-2">
            {asks.map((r) => (
              <Ask key={r.named_id} row={r} sets={book.sets} onPick={(id) => pick(r.named_id, id)} />
            ))}
          </ul>
        </div>
      )}

      {rows.length > 0 && (
        <div className={`${box} overflow-hidden`}>
          <table className="w-full text-[14px]">
            <thead>
              <tr className="border-b border-line text-left text-[12px] uppercase tracking-wider text-faint">
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
                    {r.own_plan && <span className="ml-2 text-[13px] text-faint">· your own plan comes first</span>}
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
              className="w-full border-t border-line px-4 py-2 text-left text-[13px] text-muted hover:bg-hover hover:text-ink"
            >
              {older ? 'Hide' : 'Show'} {olderCount} older strateg{olderCount === 1 ? 'y' : 'ies'}
            </button>
          )}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {book.sets.map((s) => {
          const users = usedBy(s.id)
          return (
            <article key={s.id} className={`${box} flex flex-col gap-3 p-4`}>
              <div>
                <h3 className="text-[17px] font-semibold">{s.name}</h3>
                <div className="text-[13px] text-muted">
                  <SourceLink set={s} />
                </div>
              </div>
              {lines(s).length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {lines(s).map((l) => (
                    <span key={l} className="num rounded-full bg-sunken px-2.5 py-0.5 text-[13px] text-ink">
                      {l}
                    </span>
                  ))}
                </div>
              )}
              <RuleSetRules set={s} />
              <div className="mt-auto border-t border-line pt-2 text-[13px] text-muted">
                {users.length === 0 ? 'None of your current strategies' : `Used by ${users.map((u) => u.name).join(', ')}`}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
