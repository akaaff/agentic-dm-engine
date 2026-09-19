import { useEffect, useRef, useState } from 'react'
import { getAccessKey } from '../api/accessKey'
import { WS_BASE_URL } from '../api/baseUrl'
import { getLobbyToken } from '../api/lobbyTokens'

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
  // Everything below was already reaching the browser via state_update's
  // verbatim Character.model_dump() - this interface just never declared
  // it, so it silently didn't render. See CLAUDE.md's character-sheet
  // feature writeup for the "data already on the wire" finding.
  stats: Record<'STR' | 'DEX' | 'CON' | 'INT' | 'WIS' | 'CHA', number>
  proficiency_bonus: number
  speed: number
  background: string
  persona: string | null
  monster_index: string | null
  skill_proficiencies: string[]
  saving_throw_proficiencies: string[]
  // New for issue #30's known-spell list - normalized SRD spell indices,
  // meaningful only for a "Spells Known" caster (Bard/Sorcerer); empty for
  // everyone else, including cantrips (which stay unrestricted/untracked).
  known_spells: string[]
  exhaustion_level: number
  is_dodging: boolean
  has_help_advantage: boolean
  bonus_action_used: boolean
  disengaged_this_turn: boolean
  reaction_used_this_round: boolean
  class_index: string | null
  inventory: string[]
  spell_slots: Record<string, number>
  hit_die_sides: number
  hit_dice_remaining: number
  class_resources: Record<string, number>
  fighting_style: string | null
  is_raging: boolean
  sneak_attack_used_this_turn: boolean
  death_save_successes: number
  death_save_failures: number
  level: number
  concentrating_on: string | null
  // New for the character-sheet + portrait feature (Phase 2) - PCs/companions
  // only, null for monsters and for anything created before this feature.
  race_index: string | null
  gender: string | null
  // New for equipped-weapon tracking (Phase C) - the SRD indices of the
  // character's currently active weapon set (at most 2, both light if 2) -
  // see turn_engine._resolve_equip. Attack resolution only ever matches a
  // weapon from this list, not the whole inventory.
  equipped_weapons: string[]
  equip_used_this_turn: boolean
  // New for armor swapping (issue #13) - the character's currently worn
  // armor/shield (single slots, unlike equipped_weapons's list), null when
  // unarmored/no shield. `ac` already reflects these via rules.armor_ac,
  // recomputed by turn_engine._resolve_equip whenever either changes.
  equipped_armor: string | null
  equipped_shield: string | null
  // New for issue #27's resource quick-actions - already on the wire via
  // Character.model_dump() (same "data already on the wire" pattern as the
  // rest of this interface), needed to tell "wild-shaped right now" apart
  // from "Wild Shape available" without re-deriving it from class_resources
  // alone (a Druid can have wild_shape uses left while already shaped).
  wild_shape_beast_index: string | null
}

export type TerrainType = 'floor' | 'wall' | 'difficult' | 'hazard'

export interface LiveBattleMap {
  width: number
  height: number
  terrain: TerrainType[][]
  spawn_points: Record<string, { x: number; y: number }>
}

// Mirrors src/engine/events.py's Event - already reaching the browser via
// state_update's verbatim GameState.model_dump() (same "data already on the
// wire" pattern as LiveCharacter's own fields), just never declared here.
// `payload` shape varies by `type` - see formatEvent.ts for the actual
// per-type field names, confirmed against turn_engine.py's real call sites
// rather than assumed from the EventType literal alone.
export interface LiveEvent {
  id: string
  round: number
  turn_index: number
  actor: string
  type: string
  payload: Record<string, unknown>
  timestamp: string
  narrated: boolean
}

export interface LiveGameState {
  encounter_id: string
  characters: Record<string, LiveCharacter>
  turn_order: string[]
  current_turn: number
  round: number
  status: 'in_progress' | 'victory' | 'defeat' | 'aborted'
  battle_map: LiveBattleMap | null
  events: LiveEvent[]
}

type ServerMessage =
  | { type: 'state_update'; game_state: LiveGameState }
  | { type: 'narration'; text: string }
  | { type: 'scene_narration'; text: string }
  | { type: 'scene_image'; url: string }
  | { type: 'awaiting_input'; actor: string }
  | { type: 'error'; detail: string }

export interface NarrationEntry {
  text: string
  /** "scene" is the campaign's own scene-setting text (a narrative "hook"
   * before a fight, a skill-challenge outcome, an outro) - delivered
   * between encounters, not from an individual action. "action" is the
   * ordinary per-turn narration this project has always had. */
  kind: 'scene' | 'action'
  /** The mechanical events (damage/heal amounts, crits, etc.) this
   * narration text is actually about - undefined for a "scene" entry.
   * Paired up client-side, not sent as one message: the backend always
   * broadcasts exactly one `narration` message immediately followed by one
   * `state_update` for the same action resolution (see session.py), so
   * useSessionSocket stitches them back together by tracking how many
   * events existed before each narration arrived. */
  events?: LiveEvent[]
  /** The game_state that arrived alongside this entry's state_update -
   * applied to the live `gameState` only when this entry is actually
   * revealed (see drainNext below), not when the WS message arrives. Found
   * live: once the human PC goes unconscious the backend auto-plays every
   * remaining turn (including the human's own forced death saves) in one
   * unbroken burst, so without this the sidebar/HP/defeat banner would jump
   * straight to the final outcome while the paced log was still slowly
   * revealing everything that led there. */
  gameStateSnapshot?: LiveGameState
}

// How long a revealed narration entry stays "the last thing shown" before
// the next queued one is allowed to appear - only ever adds delay when
// entries are genuinely bursty (several resolved in a chain server-side,
// e.g. companion/monster turns auto-playing after the human's own action);
// an isolated entry that arrives on its own still reveals immediately, see
// drainNext's "queue empty after this one" branch below.
const ENTRY_REVEAL_DELAY_MS = 3000

export function useSessionSocket(sessionId: string) {
  const [gameState, setGameState] = useState<LiveGameState | null>(null)
  const [narrationLog, setNarrationLog] = useState<NarrationEntry[]>([])
  const [logCaughtUp, setLogCaughtUp] = useState(true)
  const [sceneImageUrl, setSceneImageUrl] = useState<string | null>(null)
  const [awaitingActor, setAwaitingActor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  // How many of game_state.events this client has already accounted for -
  // the next state_update's new events (a plain slice from this count) are
  // the ones the most recently-received narration text is about.
  const lastEventCountRef = useRef(0)
  const pendingNarrationRef = useRef<string | null>(null)
  // Entries received but not yet revealed in narrationLog, the timer pacing
  // their reveal, and when the last one was actually shown - refs, not
  // state, since draining shouldn't itself trigger a render (only the
  // narrationLog/logCaughtUp updates it causes should).
  const entryQueueRef = useRef<NarrationEntry[]>([])
  const drainTimerRef = useRef<number | null>(null)
  const lastRevealTimeRef = useRef(-Infinity) // -Infinity, not 0: "never
  // revealed yet" must make the very first entry's own wait computation
  // come out to 0 unconditionally, not just whenever performance.now()
  // (time since page load) already happens to exceed the delay by luck.

  useEffect(() => {
    // `cancelled` guards against React StrictMode's dev-mode double-invoke
    // of effects: mount -> cleanup -> mount again. Without it, the first
    // (intentionally discarded) socket's belated onerror/onclose - firing
    // because cleanup calls close() on a still-CONNECTING socket - lands on
    // this same closure's state setters and can show a spurious "connection
    // failed" even though the second, real socket connects fine right after.
    let cancelled = false
    lastEventCountRef.current = 0
    pendingNarrationRef.current = null
    entryQueueRef.current = []
    if (drainTimerRef.current !== null) {
      window.clearTimeout(drainTimerRef.current)
      drainTimerRef.current = null
    }
    lastRevealTimeRef.current = -Infinity
    setLogCaughtUp(true)

    // Paces the queue by real elapsed time since the last reveal, not by
    // whether the queue happened to look empty at the instant a new entry
    // arrived - two entries can arrive as separate WebSocket messages only
    // milliseconds apart (a burst of resolved turns sent back to back), and
    // each one's own onmessage call runs to completion before the next
    // starts, so a purely queue-occupancy-based "is anything else pending"
    // check would see an empty queue every single time and reveal every
    // entry instantly. Scheduling the next reveal for whatever time remains
    // of the delay (0 if enough real time has already passed) fixes that
    // without adding any wait when entries genuinely arrive spaced out.
    function scheduleDrain() {
      if (drainTimerRef.current !== null) return // already scheduled
      const next = entryQueueRef.current[0]
      if (!next) {
        setLogCaughtUp(true)
        return
      }
      const elapsed = performance.now() - lastRevealTimeRef.current
      const wait = Math.max(0, ENTRY_REVEAL_DELAY_MS - elapsed)
      drainTimerRef.current = window.setTimeout(() => {
        drainTimerRef.current = null
        const entry = entryQueueRef.current.shift()
        if (entry) {
          setNarrationLog((prev) => [...prev, entry])
          // Apply the state this entry belongs to now, not when it arrived -
          // keeps the sidebar/HP/banner in lockstep with the paced log.
          if (entry.gameStateSnapshot) setGameState(entry.gameStateSnapshot)
          lastRevealTimeRef.current = performance.now()
        }
        scheduleDrain() // schedules the next one, or marks caught up if none left
      }, wait)
    }

    function enqueueEntry(entry: NarrationEntry) {
      entryQueueRef.current.push(entry)
      setLogCaughtUp(false)
      scheduleDrain()
    }

    // Issue #42: a plain WebSocket can't carry a custom header, so the
    // passphrase (when one is stored) rides along as a query param instead -
    // see api/ws/session.py's session_websocket for the matching check.
    // Issue #44/#45: same story for a lobby's personal player token, which
    // identifies which character this connection controls in a session with
    // 2+ human seats (a single-seat session needs neither the frontend nor
    // the server to know about a token at all - see session_websocket's own
    // single-seat exemption).
    const params = new URLSearchParams()
    const accessKey = getAccessKey()
    if (accessKey) params.set('key', accessKey)
    const lobbyToken = getLobbyToken(sessionId)
    if (lobbyToken) params.set('token', lobbyToken)
    const query = params.toString() ? `?${params.toString()}` : ''
    const ws = new WebSocket(`${WS_BASE_URL}/ws/session/${sessionId}${query}`)
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
        case 'state_update': {
          const newEvents = message.game_state.events.slice(lastEventCountRef.current)
          lastEventCountRef.current = message.game_state.events.length
          const pendingText = pendingNarrationRef.current
          pendingNarrationRef.current = null
          if (pendingText || newEvents.length > 0 || entryQueueRef.current.length > 0) {
            // Held back until this entry is actually revealed (drainNext) -
            // see NarrationEntry.gameStateSnapshot's docstring. The queue
            // check matters even when THIS update has no text/events of its
            // own: found live going straight from a campaign's pre-combat
            // hook into its first fight - the connect flow's own trailing
            // state_update (sent once encounter setup finishes) can be a
            // genuine duplicate of one already broadcast moments earlier
            // (same event count, no new narration), which used to qualify
            // for the "nothing paces me" fast path below and apply
            // immediately - flipping gameState (and so the combat-grid-vs-
            // scene-image panel, which reads battle_map/status straight off
            // it) to the fully-resolved combat state while the hook
            // narration ahead of it in the queue was still being revealed
            // one entry at a time. Whether THIS message needs pacing isn't
            // the right question - whether anything else is already ahead
            // of it in the queue is.
            enqueueEntry({
              text: pendingText ?? '',
              kind: 'action',
              events: newEvents,
              gameStateSnapshot: message.game_state,
            })
          } else {
            // Nothing of its own to pace, and nothing already queued for it
            // to jump ahead of - safe to apply right away.
            setGameState(message.game_state)
          }
          break
        }
        case 'narration':
          // Stashed, not pushed yet - the state_update broadcast that
          // always immediately follows (see session.py) carries the
          // mechanical events this same narration is about, and both land
          // in the log as one combined entry.
          pendingNarrationRef.current = message.text || null
          break
        case 'scene_narration':
          if (message.text) {
            enqueueEntry({ text: message.text, kind: 'scene' })
          }
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
      if (drainTimerRef.current !== null) {
        window.clearTimeout(drainTimerRef.current)
        drainTimerRef.current = null
      }
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

  // Party-wide, not turn-based (issue #28) - only reachable between
  // encounters (gameState.status === 'victory'), so unlike sendPlayerAction/
  // sendPlayerMove these don't touch awaitingActor at all.
  function sendRest(restType: 'short' | 'long') {
    wsRef.current?.send(JSON.stringify({ type: 'rest', rest_type: restType }))
    setError(null)
  }

  function sendContinueCampaign() {
    wsRef.current?.send(JSON.stringify({ type: 'continue_campaign' }))
    setError(null)
  }

  return {
    gameState,
    narrationLog,
    logCaughtUp,
    sceneImageUrl,
    awaitingActor,
    error,
    connected,
    sendPlayerAction,
    sendPlayerMove,
    sendRest,
    sendContinueCampaign,
  }
}
