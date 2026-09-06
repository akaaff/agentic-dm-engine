import { useEffect, useRef, useState } from 'react'

// Mirrors the JSON shape of src/engine/state.py's Character/GameState -
// only the fields the UI actually renders, not a full 1:1 port of every
// engine field (spell_slots, skill_proficiencies etc. aren't shown yet).
export interface LiveCondition {
  name: string
  duration_rounds: number | null
  source: string
}

export interface LiveCharacter {
  id: string
  name: string
  is_pc: boolean
  is_companion: boolean
  hp: number
  max_hp: number
  ac: number
  position: { x: number; y: number }
  conditions: LiveCondition[]
  is_dead: boolean
  is_stable: boolean
  race: string
  class_: string
}

export type TerrainType = 'floor' | 'wall' | 'difficult' | 'hazard'

export interface LiveBattleMap {
  width: number
  height: number
  terrain: TerrainType[][]
  spawn_points: Record<string, { x: number; y: number }>
}

export interface LiveGameState {
  encounter_id: string
  characters: Record<string, LiveCharacter>
  turn_order: string[]
  current_turn: number
  round: number
  status: 'in_progress' | 'victory' | 'defeat' | 'aborted'
  battle_map: LiveBattleMap | null
}

type ServerMessage =
  | { type: 'state_update'; game_state: LiveGameState }
  | { type: 'narration'; text: string }
  | { type: 'scene_image'; url: string }
  | { type: 'awaiting_input'; actor: string }
  | { type: 'error'; detail: string }

const WS_BASE_URL = 'ws://localhost:8000'

export function useSessionSocket(sessionId: string) {
  const [gameState, setGameState] = useState<LiveGameState | null>(null)
  const [narrationLog, setNarrationLog] = useState<string[]>([])
  const [sceneImageUrl, setSceneImageUrl] = useState<string | null>(null)
  const [awaitingActor, setAwaitingActor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    // `cancelled` guards against React StrictMode's dev-mode double-invoke
    // of effects: mount -> cleanup -> mount again. Without it, the first
    // (intentionally discarded) socket's belated onerror/onclose - firing
    // because cleanup calls close() on a still-CONNECTING socket - lands on
    // this same closure's state setters and can show a spurious "connection
    // failed" even though the second, real socket connects fine right after.
    let cancelled = false
    const ws = new WebSocket(`${WS_BASE_URL}/ws/session/${sessionId}`)
    wsRef.current = ws

    ws.onopen = () => {
      if (!cancelled) setConnected(true)
    }
    ws.onclose = () => {
      if (!cancelled) setConnected(false)
    }
    ws.onerror = () => {
      if (!cancelled) setError('Connection to the game server failed')
    }

    ws.onmessage = (event: MessageEvent<string>) => {
      if (cancelled) return
      const message = JSON.parse(event.data) as ServerMessage
      switch (message.type) {
        case 'state_update':
          setGameState(message.game_state)
          break
        case 'narration':
          if (message.text) setNarrationLog((prev) => [...prev, message.text])
          break
        case 'scene_image':
          setSceneImageUrl(message.url)
          break
        case 'awaiting_input':
          setAwaitingActor(message.actor)
          break
        case 'error':
          setError(message.detail)
          break
      }
    }

    return () => {
      cancelled = true
      ws.close()
    }
  }, [sessionId])

  function sendPlayerAction(text: string) {
    wsRef.current?.send(JSON.stringify({ type: 'player_action', text }))
    setAwaitingActor(null)
    setError(null)
  }

  function sendPlayerMove(to: { x: number; y: number }) {
    wsRef.current?.send(JSON.stringify({ type: 'player_move', to }))
    setAwaitingActor(null)
    setError(null)
  }

  return {
    gameState,
    narrationLog,
    sceneImageUrl,
    awaitingActor,
    error,
    connected,
    sendPlayerAction,
    sendPlayerMove,
  }
}
