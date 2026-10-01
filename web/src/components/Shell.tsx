import type { ReactNode } from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

export type TabId = 'brief' | 'ask' | 'legs' | 'strategies' | 'positions' | 'tom' | 'whatif' | 'performance' | 'rules' | 'history' | 'grouping' | 'activity' | 'health'

/* Order is by what a trading day actually needs.

   What is open and what needs a decision comes first. The record of what
   already happened — closed trades, the event log — goes last, and the
   housekeeping that is only touched when something needs regrouping sits
   between them behind a divider. The app opens on Positions. */
const TABS: { id: TabId; label: string; hint: string; group?: 'later' }[] = [
  { id: 'positions', label: 'Positions', hint: 'What you hold, sorted by what needs attention' },
  { id: 'tom', label: 'Tom Analysis', hint: "Your book against Tom King's 2026 trading plan" },
  { id: 'brief', label: 'Brief', hint: "This morning's read on the book" },
  { id: 'strategies', label: 'Strategies', hint: 'The strategies you named, and how they do' },
  { id: 'whatif', label: 'What if', hint: 'Move price, volatility and time, and see the book' },
  { id: 'ask', label: 'Ask', hint: 'Ask Claude about your positions' },
  { id: 'performance', label: 'Performance', hint: 'Win rate and expectancy per strategy' },
  { id: 'rules', label: 'Rules', hint: 'How often you follow your own rules' },
  { id: 'legs', label: 'Legs', hint: 'Every open leg, one line each', group: 'later' },
  { id: 'grouping', label: 'Grouping', hint: 'Which legs belong to the same trade', group: 'later' },
  { id: 'history', label: 'History', hint: 'Closed trades', group: 'later' },
  { id: 'activity', label: 'Activity', hint: 'What changed, and when', group: 'later' },
  { id: 'health', label: 'Health', hint: 'Connection to tastytrade', group: 'later' },
]

function useTheme() {
  const [theme, setTheme] = useState<'system' | 'light' | 'dark'>(() => {
    try {
      return (localStorage.getItem('td-theme') as 'system' | 'light' | 'dark') ?? 'system'
    } catch {
      return 'system'
    }
  })

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('td-theme', theme)
    } catch {
      /* private window — the choice just will not persist */
    }
  }, [theme])

  return { theme, setTheme }
}

export function Shell({
  tab,
  onTab,
  children,
  status,
}: {
  tab: TabId
  onTab: (t: TabId) => void
  children: ReactNode
  status?: ReactNode
}) {
  const { theme, setTheme } = useTheme()

  return (
    <div className="flex min-h-full flex-col">
      {/* The frosted bar. It sits over the content rather than pushing it down,
          and the blur is what tells you there is a page moving underneath. */}
      <header
        className="sticky top-0 z-20 border-b border-line"
        style={{
          background: 'var(--glass-nav)',
          backdropFilter: 'var(--glass-blur)',
          WebkitBackdropFilter: 'var(--glass-blur)',
        }}
      >
        <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-8 py-4">
          <div className="flex items-baseline gap-2.5">
            <span className="display gold-text text-[22px]">Tasty Desk</span>
            <span className="label hidden sm:inline">
              read only
            </span>
          </div>

          <TabBar tab={tab} onTab={onTab} />

          <div className="flex shrink-0 items-center gap-3">
            {status}
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : theme === 'light' ? 'system' : 'dark')}
              title={`Theme: ${theme}. Click to change.`}
              className="rounded-full px-3 py-1.5 text-[13px] uppercase tracking-[0.08em] text-faint hover:bg-hover hover:text-ink"
            >
              {theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : 'Auto'}
            </button>
          </div>
        </div>
      </header>

      {/* Keyed on the tab so the page it contains arrives rather than blinks. */}
      <main key={tab} className="rise mx-auto w-full max-w-[1400px] flex-1 px-8 py-16">
        {children}
      </main>

      <footer className="mt-16 px-8 py-12">
        <div className="mx-auto max-w-[1400px] border-t border-line pt-4 text-[13px] text-faint">
          Runs on this Mac. Credentials live in the macOS Keychain, the API key is read-only, and
          nothing leaves the machine.
        </div>
      </footer>
    </div>
  )
}


/* The tabs, on one line, never scrolled.

   The bar used to scroll sideways when the window was too narrow for every
   tab, with a scrollbar under it and tabs cut off at the edge. Now the tabs
   that do not fit move into a "More" menu, last first, so the ones used
   every day stay in view and nothing is ever half-shown. */
function TabBar({ tab, onTab }: { tab: TabId; onTab: (t: TabId) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const ruler = useRef<HTMLDivElement>(null)
  const [fits, setFits] = useState(TABS.length)
  const [open, setOpen] = useState(false)

  const measure = useCallback(() => {
    const room = box.current?.clientWidth ?? 0
    const items = Array.from(ruler.current?.children ?? []) as HTMLElement[]
    if (!room || items.length === 0) return
    // +2 for the gap between pills.
    const widths = items.slice(0, TABS.length).map((el) => el.offsetWidth + 2)
    const more = items[TABS.length]?.offsetWidth ?? 90
    const all = widths.reduce((a, b) => a + b, 0)
    if (all <= room) {
      setFits(TABS.length)
      return
    }
    let used = more
    let n = 0
    while (n < widths.length && used + widths[n] <= room) {
      used += widths[n]
      n += 1
    }
    setFits(n)
  }, [])

  // Measured after every render as well as on resize: the status pill beside
  // the bar arrives after the first paint and narrows it without the window
  // changing size, and a bar measured before that ran under it.
  useLayoutEffect(() => {
    measure()
  })
  useEffect(() => {
    const observer = new ResizeObserver(measure)
    if (box.current) observer.observe(box.current)
    window.addEventListener('resize', measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [measure])

  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [open])

  const shown = TABS.slice(0, fits)
  const hidden = TABS.slice(fits)
  const current = hidden.find((t) => t.id === tab)

  const pill = (active: boolean, later?: boolean) =>
    `relative whitespace-nowrap rounded-full px-3.5 py-1.5 text-[15px] ${
      active
        ? 'bg-accent font-medium text-white'
        : later
          ? 'text-faint hover:bg-hover hover:text-ink'
          : 'text-muted hover:bg-hover hover:text-ink'
    }`

  return (
    <div ref={box} className="relative min-w-0 flex-1">
      {/* Every tab drawn once, invisibly, to know how wide each one is. */}
      <div ref={ruler} aria-hidden className="pointer-events-none invisible absolute left-0 top-0 flex h-0 w-max overflow-hidden [&>*]:shrink-0">
        {TABS.map((t, i) => (
          <span key={t.id} className="flex items-center">
            {t.group === 'later' && TABS[i - 1]?.group !== 'later' && <span className="mx-2 h-4 w-px" />}
            <span className={`${pill(false)} font-medium`}>{t.label}</span>
          </span>
        ))}
        <span className={pill(false)}>More ▾</span>
      </div>

      <nav className="flex items-center gap-0.5">
        {shown.map((t, i) => (
          <span key={t.id} className="flex items-center">
            {t.group === 'later' && TABS[i - 1]?.group !== 'later' && (
              <span aria-hidden className="mx-2 h-4 w-px bg-line" />
            )}
            {/* The live tab is a filled pill rather than an underline —
                the segmented-control register, where the selection is a
                surface you moved to and not a mark under a word. */}
            <button
              onClick={() => onTab(t.id)}
              title={t.hint}
              aria-current={tab === t.id ? 'page' : undefined}
              className={pill(tab === t.id, t.group === 'later')}
            >
              {t.label}
            </button>
          </span>
        ))}

        {hidden.length > 0 && (
          <span className="relative">
            <button
              onClick={(e) => {
                e.stopPropagation()
                setOpen(!open)
              }}
              aria-expanded={open}
              className={pill(current !== undefined)}
            >
              {current ? current.label : 'More'} ▾
            </button>
            {open && (
              <div className="absolute left-0 top-full z-30 mt-2 min-w-[12rem] rounded-card border border-line bg-raised p-1.5 shadow-[var(--shadow-md)]">
                {hidden.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => {
                      onTab(t.id)
                      setOpen(false)
                    }}
                    title={t.hint}
                    className={`block w-full rounded-sm px-3 py-2 text-left text-[15px] ${
                      tab === t.id ? 'bg-accent-soft font-medium text-accent' : 'text-ink hover:bg-hover'
                    }`}
                  >
                    {t.label}
                    <div className="text-[12px] text-muted">{t.hint}</div>
                  </button>
                ))}
              </div>
            )}
          </span>
        )}
      </nav>
    </div>
  )
}
