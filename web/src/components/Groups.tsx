import { useState } from 'react'
import { api } from '../lib/api'
import { useAsync } from '../lib/useAsync'
import type { StrategyGroup } from '../types'

/* Strategies of strategies. A group such as Spec holds several named
   strategies; the Strategies and Performance pages can be narrowed to one
   group and read it as a single book. Saved as a setting, so both pages and
   every browser see the same groups. */

export function useGroups() {
  const settings = useAsync(() => api.settings(), [])
  const groups = settings.data?.strategy_groups ?? []
  async function save(next: StrategyGroup[]) {
    await api.setSetting('strategy_groups', JSON.stringify(next))
    settings.reload()
  }
  return { groups, save, loading: settings.loading && !settings.data }
}

/* Which group a page is showing, remembered per page in this browser. */
export function useChosenGroup(page: string): [string | null, (id: string | null) => void] {
  const key = `td.group.${page}`
  const [chosen, setChosen] = useState<string | null>(() => {
    try {
      return localStorage.getItem(key)
    } catch {
      return null
    }
  })
  function choose(id: string | null) {
    setChosen(id)
    try {
      if (id) localStorage.setItem(key, id)
      else localStorage.removeItem(key)
    } catch {
      /* remembered as a convenience only */
    }
  }
  return [chosen, choose]
}

function slug(name: string, taken: string[]): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'group'
  let id = base
  for (let n = 2; taken.includes(id); n++) id = `${base}-${n}`
  return id
}

export function GroupBar({
  groups,
  value,
  onChange,
  onSave,
  strategies,
  allLabel,
}: {
  groups: StrategyGroup[]
  value: string | null
  onChange: (id: string | null) => void
  onSave: (next: StrategyGroup[]) => Promise<void>
  /** Every named strategy that can go in a group: id and a label to tick. */
  strategies: { id: string; label: string }[]
  allLabel: string
}) {
  const [editing, setEditing] = useState(false)
  const current = groups.find((g) => g.id === value) ?? null
  const pill = (on: boolean) =>
    `rounded-full border px-4 py-1.5 text-[15px] font-medium transition-colors ${
      on ? 'border-accent bg-accent text-white' : 'border-line bg-raised text-ink hover:bg-hover'
    }`

  async function addGroup() {
    const name = window.prompt('Name the new group')?.trim()
    if (!name) return
    const id = slug(name, groups.map((g) => g.id))
    await onSave([...groups, { id, name, members: [] }])
    onChange(id)
    setEditing(true)
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => onChange(null)} className={pill(value === null)}>
          {allLabel}
        </button>
        {groups.map((g) => (
          <button key={g.id} onClick={() => onChange(g.id)} className={pill(value === g.id)}>
            {g.name} <span className="opacity-70">· {g.members.length}</span>
          </button>
        ))}
        <button onClick={addGroup} className="rounded-full px-3 py-1.5 text-[14px] text-muted hover:bg-hover hover:text-ink">
          + New group
        </button>
        {current && (
          <button
            onClick={() => setEditing(!editing)}
            className="ml-auto rounded-card border border-line px-3.5 py-1.5 text-[14px] text-muted hover:bg-hover hover:text-ink"
          >
            {editing ? 'Done' : `Choose what's in ${current.name}`}
          </button>
        )}
      </div>
      {current && editing && (
        <GroupEditor
          group={current}
          strategies={strategies}
          onSave={(g) => onSave(groups.map((x) => (x.id === g.id ? g : x)))}
          onDelete={
            current.id === 'spec'
              ? undefined
              : async () => {
                  if (!window.confirm(`Delete the group "${current.name}"? The strategies in it are not touched.`)) return
                  await onSave(groups.filter((x) => x.id !== current.id))
                  onChange(null)
                  setEditing(false)
                }
          }
        />
      )}
    </div>
  )
}

function GroupEditor({
  group,
  strategies,
  onSave,
  onDelete,
}: {
  group: StrategyGroup
  strategies: { id: string; label: string }[]
  onSave: (g: StrategyGroup) => Promise<void>
  onDelete?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  async function toggle(id: string) {
    const members = group.members.includes(id) ? group.members.filter((m) => m !== id) : [...group.members, id]
    setBusy(true)
    setProblem(null)
    try {
      await onSave({ ...group, members })
    } catch (e) {
      setProblem(e instanceof Error ? e.message : 'Could not save that.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="rounded-card border border-line bg-raised px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-[15px]">
          Tick the strategies that belong in <span className="font-semibold">{group.name}</span>.
        </div>
        {onDelete && (
          <button onClick={onDelete} className="text-[13px] text-loss hover:underline">
            Delete this group
          </button>
        )}
      </div>
      <ul className="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {strategies.map((s) => (
          <li key={s.id}>
            <label className="flex cursor-pointer items-center gap-2.5 text-[15px]">
              <input
                type="checkbox"
                disabled={busy}
                checked={group.members.includes(s.id)}
                onChange={() => toggle(s.id)}
                className="h-4 w-4 accent-[var(--accent)]"
              />
              <span className="truncate">{s.label}</span>
            </label>
          </li>
        ))}
      </ul>
      {problem && <p className="mt-2 text-[13px] text-loss">{problem}</p>}
    </div>
  )
}
