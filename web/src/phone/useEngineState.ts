import { useEffect, useState } from 'react'
import { engineState, onEngineState, type EngineState } from './engine'

export function useEngineState(): EngineState {
  const [state, setState] = useState(engineState)
  useEffect(() => onEngineState(setState), [])
  return state
}
