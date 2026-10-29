import './demo-shim'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import App from './App.tsx'
import { onPhone, routeApiToEngine, startEngine } from './phone/engine'
import { PhoneRoot } from './phone/PhoneRoot'

// On the iPhone the pages are answered by the engine inside the app.
if (onPhone) {
  routeApiToEngine()
  void startEngine().catch(() => {})
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {onPhone ? <PhoneRoot /> : <App />}
  </StrictMode>,
)
