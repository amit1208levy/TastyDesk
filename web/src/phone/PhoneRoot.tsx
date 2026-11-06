import { useEffect, useState } from 'react'
import App from '../App'
import { useEngineState } from './useEngineState'
import { hasSavedAnswers, loadSavedAnswers, onSaved, savedState, type SavedState } from './engine'

/* The iPhone app: the same App as the Mac. Starting the engine inside it takes
   a few seconds; if the app has answers saved from last time it opens on them
   at once, under a bar that says how old they are, and otherwise it says it
   is starting rather than showing pages that are all waiting at once. */
export function PhoneRoot() {
  const state = useEngineState()
  const [savedReady, setSavedReady] = useState(false)
  useEffect(() => {
    void loadSavedAnswers().then(() => setSavedReady(true))
  }, [])

  if (state.phase === 'ready' || (state.phase === 'starting' && savedReady && hasSavedAnswers()))
    return (
      <div className="safe-sides">
        <SavedBar />
        <App />
      </div>
    )
  return (
    <div className="safe-area flex min-h-screen items-center justify-center px-6">
      {state.phase === 'starting' ? (
        <div className="flex items-center gap-2.5 text-copy text-faint">
          <span className="h-1.5 w-1.5 rounded-full bg-accent breathing" />
          <span className="uppercase tracking-[0.12em]">Starting Tasty Desk</span>
        </div>
      ) : (
        <div className="max-w-md rounded-card border border-danger/40 bg-danger-soft px-4 py-3">
          <div className="text-copy font-medium text-danger">Tasty Desk could not start.</div>
          <div className="mt-1 text-body text-danger/85">{state.error}</div>
          <button
            onClick={() => location.reload()}
            className="mt-2 rounded-sm border border-danger/40 px-2 py-1 text-body text-danger"
          >
            Try again
          </button>
        </div>
      )}
    </div>
  )
}

/** Shown while any number on screen is a saved one: from when, and that it is updating. */
function SavedBar() {
  const [saved, setSaved] = useState<SavedState>(savedState)
  useEffect(() => onSaved(setSaved), [])
  if (saved.showingFrom === null) return null
  const at = new Date(saved.showingFrom).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return (
    <div className="fixed inset-x-0 bottom-0 z-30 flex justify-center pb-[max(env(safe-area-inset-bottom),12px)]">
      <div className="flex items-center gap-2 rounded-full border border-line bg-raised-solid px-3.5 py-1.5 text-[13px] text-muted shadow-[var(--shadow-md)]">
        <span className="h-1.5 w-1.5 rounded-full bg-accent breathing" />
        Saved numbers from {at} · updating
      </div>
    </div>
  )
}
