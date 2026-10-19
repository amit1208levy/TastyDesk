import App from '../App'
import { useEngineState } from './useEngineState'

/* The iPhone app: the same App as the Mac, once the engine inside it is up.
   Starting Python takes a few seconds, so it says so rather than showing
   pages that are all waiting at once. */
export function PhoneRoot() {
  const state = useEngineState()
  if (state.phase === 'ready') return <App />
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      {state.phase === 'starting' ? (
        <div className="flex items-center gap-2.5 text-[15px] text-faint">
          <span className="h-1.5 w-1.5 rounded-full bg-accent breathing" />
          <span className="uppercase tracking-[0.12em]">Starting Tasty Desk</span>
        </div>
      ) : (
        <div className="max-w-md rounded-card border border-danger/40 bg-danger-soft px-4 py-3">
          <div className="text-[16px] font-medium text-danger">Tasty Desk could not start.</div>
          <div className="mt-1 text-[14px] text-danger/85">{state.error}</div>
          <button
            onClick={() => location.reload()}
            className="mt-2 rounded-sm border border-danger/40 px-2 py-1 text-[14px] text-danger"
          >
            Try again
          </button>
        </div>
      )}
    </div>
  )
}
