import { useEffect, useState } from 'react'
import App from './App.tsx'
import { PhonePairing } from './components/PhonePairing'
import { loadPairing, onPhone, whenDisconnected } from './lib/connection'

/* On the Mac this is just the app. On the phone it first needs to know which
   Mac to read, and goes back to asking whenever that Mac turns it away. */
export default function Root() {
  const [state, setState] = useState<'loading' | 'paired' | 'unpaired' | 'refused'>(
    onPhone ? 'loading' : 'paired',
  )

  useEffect(() => {
    if (!onPhone) return
    whenDisconnected((why) => setState(why === 'refused' ? 'refused' : 'unpaired'))
    loadPairing()
      .then((ok) => setState(ok ? 'paired' : 'unpaired'))
      .catch(() => setState('unpaired'))
  }, [])

  if (state === 'loading') return null
  if (state !== 'paired') {
    return <PhonePairing refused={state === 'refused'} onPaired={() => setState('paired')} />
  }
  return <App />
}
