import type { ReactNode } from 'react'
import { useEffect, useState } from 'react'

export type TabId = 'positions' | 'performance' | 'rules' | 'history' | 'health'

const TABS: { id: TabId; label: string; hint: string }[] = [
  { id: 'positions', label: 'Positions', hint: 'Open strategies, sorted by what needs attention' },
  { id: 'performance', label: 'Performance', hint: 'Win rate and expectancy per strategy' },
  { id: 'rules', label: 'Rules', hint: 'How often you follow your own rules' },
  { id: 'history', label: 'History', hint: 'Closed trades' },
  { id: 'health', label: 'Health', hint: 'Connection to tastytrade' },
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
      <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-[1400px] items-center gap-4 px-5 py-2.5">
          <div className="flex items-baseline gap-2">
            <span className="text-[15px] font-semibold tracking-tight">Tasty Desk</span>
            <span className="hidden text-[11px] text-faint sm:inline">read-only</span>
          </div>

          <nav className="flex items-center gap-0.5 overflow-x-auto">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => onTab(t.id)}
                title={t.hint}
                className={`whitespace-nowrap rounded-sm px-2.5 py-1 text-[13px] transition-colors ${
                  tab === t.id ? 'bg-sunken font-medium text-ink' : 'text-muted hover:bg-hover hover:text-ink'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {status}
            <button
              onClick={() => setTheme(theme === 'dark' ? 'light' : theme === 'light' ? 'system' : 'dark')}
              title={`Theme: ${theme}. Click to change.`}
              className="rounded-sm px-2 py-1 text-xs text-muted transition-colors hover:bg-hover hover:text-ink"
            >
              {theme === 'dark' ? 'Dark' : theme === 'light' ? 'Light' : 'Auto'}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-5 py-5">{children}</main>

      <footer className="border-t border-line px-5 py-3">
        <div className="mx-auto max-w-[1400px] text-[11px] text-faint">
          Runs on this Mac. Credentials live in the macOS Keychain, the API key is read-only, and nothing
          leaves the machine.
        </div>
      </footer>
    </div>
  )
}
