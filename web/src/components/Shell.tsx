import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'

export type TabId = 'brief' | 'ask' | 'legs' | 'strategies' | 'positions' | 'whatif' | 'performance' | 'rules' | 'history' | 'grouping' | 'activity' | 'health'

/* Order is by what a trading day actually needs.

   What is open and what needs a decision comes first. The record of what
   already happened — closed trades, the event log — goes last, and the
   housekeeping that is only touched when something needs regrouping sits
   between them behind a divider. The app opens on Positions. */
const TABS: { id: TabId; label: string; hint: string; group?: 'later' }[] = [
  { id: 'positions', label: 'Positions', hint: 'What you hold, sorted by what needs attention' },
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

          <nav className="flex items-center gap-0.5 overflow-x-auto">
            {TABS.map((t, i) => (
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
                  className={`relative whitespace-nowrap rounded-full px-3.5 py-1.5 text-[15px] ${
                    tab === t.id
                      ? 'bg-accent font-medium text-white'
                      : t.group === 'later'
                        ? 'text-faint hover:bg-hover hover:text-ink'
                        : 'text-muted hover:bg-hover hover:text-ink'
                  }`}
                >
                  {t.label}
                </button>
              </span>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
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
