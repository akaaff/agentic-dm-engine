import { useEffect, useState } from 'react'
import { getAccessKey } from './api/accessKey'
import { api, ApiError } from './api/client'
import {
  clearLastSessionId,
  addLobbyToken,
  getLastSessionId,
  getLobbyTokens,
  setLastSessionId,
} from './api/lobbyTokens'
import AccessGate from './screens/AccessGate'
import CharacterCreator from './screens/CharacterCreator'
import CampaignSelect from './screens/CampaignSelect'
import Home from './screens/Home'
import JoinByCode from './screens/JoinByCode'
import Lobby from './screens/Lobby'
import LivePlay from './screens/LivePlay'

// Issue #45: campaign selection creates an open lobby immediately (not
// "assemble everyone, then start") - a player then creates their character
// scoped to that lobby (or, if joining with an already-known token, resumes
// straight to their existing one), waits in the lobby for others, and
// finally reaches live play once anyone starts it. Character creation
// happens *after* joining, per the issue's own clarified design.
type Flow =
  | { screen: 'home' }
  | { screen: 'campaign' }
  | { screen: 'join_code' }
  | { screen: 'character'; sessionId: string }
  | { screen: 'lobby'; sessionId: string; characterIds: string[] }
  | { screen: 'live'; sessionId: string; characterIds: string[] }

// Every token a browser holds for a session is one character it controls -
// confirm each is still known to the lobby rather than assuming (the server
// may have been reset, or a stored value gone stale), keeping the ones that
// are. Order follows the stored tokens, i.e. the order the characters joined.
async function resumeSeats(sessionId: string, tokens: string[]): Promise<string[]> {
  const results = await Promise.allSettled(
    tokens.map((token) => api.joinLobby(sessionId, { token }))
  )
  return results.flatMap((r) => (r.status === 'fulfilled' ? [r.value.character_id] : []))
}

function App() {
  const [flow, setFlow] = useState<Flow>({ screen: 'home' })
  const [working, setWorking] = useState(false)
  const [workingError, setWorkingError] = useState<string | null>(null)
  // Issue #42: 'checking' avoids a flash of the gate screen (or the real
  // app) before we know whether the backend even requires a passphrase, or
  // whether an already-stored one from a prior visit is still valid.
  const [gate, setGate] = useState<'checking' | 'locked' | 'unlocked'>('checking')
  // Live-found: a plain page refresh mid-game always dropped back to Home,
  // even though everything needed to resume (lobbyTokens.ts's stored
  // token + last-session-id) was already sitting in localStorage - nothing
  // on mount ever looked it up. 'resuming' gates the very first render the
  // same way 'checking' already does, so there's no flash of Home before
  // this redirects straight back into the game.
  const [resuming, setResuming] = useState(true)

  useEffect(() => {
    api
      .checkHealth()
      .then(async ({ passphrase_required }) => {
        if (!passphrase_required) {
          setGate('unlocked')
          return
        }
        const stored = getAccessKey()
        if (stored) {
          try {
            await api.verifyAccessKey(stored)
            setGate('unlocked')
            return
          } catch {
            // Stored key no longer valid (e.g. the operator rotated it) -
            // fall through to the gate screen below.
          }
        }
        setGate('locked')
      })
      .catch(() => setGate('unlocked')) // /health itself unreachable - let the rest of the app surface that error normally rather than getting stuck behind a gate that can never resolve.
  }, [])

  useEffect(() => {
    if (gate !== 'unlocked') return
    const lastSessionId = getLastSessionId()
    const tokens = lastSessionId ? getLobbyTokens(lastSessionId) : []
    if (!lastSessionId || tokens.length === 0) {
      setResuming(false)
      return
    }
    ;(async () => {
      try {
        const status = await api.getLobbyStatus(lastSessionId)
        const characterIds = await resumeSeats(lastSessionId, tokens)
        if (characterIds.length === 0) return
        setFlow(
          status.status === 'in_progress'
            ? { screen: 'live', sessionId: lastSessionId, characterIds }
            : { screen: 'lobby', sessionId: lastSessionId, characterIds }
        )
      } catch {
        // Session gone, token invalid, server state wiped by a restart
        // (see DECISIONS.md - mid-fight progress doesn't survive a
        // backend restart) - fall back to Home cleanly rather than
        // getting stuck on a blank screen.
      } finally {
        setResuming(false)
      }
    })()
  }, [gate])

  if (gate === 'checking' || resuming) {
    return null
  }

  if (gate === 'locked') {
    return <AccessGate onUnlocked={() => setGate('unlocked')} />
  }

  if (flow.screen === 'home') {
    return (
      <Home
        onHost={() => setFlow({ screen: 'campaign' })}
        onJoin={() => {
          setWorkingError(null)
          setFlow({ screen: 'join_code' })
        }}
      />
    )
  }

  if (flow.screen === 'campaign') {
    return (
      <CampaignSelect
        starting={working}
        startError={workingError}
        onBack={() => setFlow({ screen: 'home' })}
        onStart={async (campaignId) => {
          setWorking(true)
          setWorkingError(null)
          try {
            const { session_id } = await api.createLobby(campaignId)
            setFlow({ screen: 'character', sessionId: session_id })
          } catch (err) {
            setWorkingError(err instanceof ApiError ? err.message : 'Failed to reach the server')
          } finally {
            setWorking(false)
          }
        }}
      />
    )
  }

  if (flow.screen === 'join_code') {
    return (
      <JoinByCode
        joining={working}
        joinError={workingError}
        onBack={() => setFlow({ screen: 'home' })}
        onJoin={async (code) => {
          setWorking(true)
          setWorkingError(null)
          try {
            const status = await api.getLobbyStatus(code)
            // Resuming: confirm every stored token is still known to this lobby
            // (the server may have been reset, or a stored value gone stale);
            // none left means this browser is a new player here.
            const characterIds = await resumeSeats(code, getLobbyTokens(code))
            if (characterIds.length > 0) {
              setLastSessionId(code)
              setFlow(
                status.status === 'in_progress'
                  ? { screen: 'live', sessionId: code, characterIds }
                  : { screen: 'lobby', sessionId: code, characterIds }
              )
            } else {
              setFlow({ screen: 'character', sessionId: code })
            }
          } catch (err) {
            setWorkingError(
              err instanceof ApiError
                ? err.message
                : 'Failed to reach the server'
            )
          } finally {
            setWorking(false)
          }
        }}
      />
    )
  }

  if (flow.screen === 'character') {
    const { sessionId } = flow
    return (
      <CharacterCreator
        onCreated={async (characters) => {
          // The characters are already created and persisted at this point -
          // each one claims its own lobby seat (and gets its own token), and
          // joinLobby's idempotent-retry handling (issue #44: a repeat join for
          // the same character_id returns the same token rather than
          // erroring) means one automatic retry per character on a transient
          // failure is safe, without walking back through character creation
          // (which would fail outright - the id already exists).
          const joined: string[] = []
          let lastError: string | null = null
          for (const character of characters) {
            for (let attempt = 0; attempt < 2; attempt++) {
              try {
                const { token, character_id } = await api.joinLobby(sessionId, {
                  character_id: character.id,
                })
                addLobbyToken(sessionId, token)
                joined.push(character_id)
                break
              } catch (err) {
                lastError =
                  err instanceof ApiError ? err.message : 'Failed to reach the server'
              }
            }
          }
          if (joined.length === 0) {
            setWorkingError(lastError)
            setFlow({ screen: 'join_code' })
            return
          }
          setLastSessionId(sessionId)
          setFlow({ screen: 'lobby', sessionId, characterIds: joined })
        }}
      />
    )
  }

  if (flow.screen === 'lobby') {
    const { sessionId, characterIds } = flow
    return (
      <Lobby
        sessionId={sessionId}
        onStarted={() => setFlow({ screen: 'live', sessionId, characterIds })}
      />
    )
  }

  return (
    <LivePlay
      sessionId={flow.sessionId}
      myCharacterIds={flow.characterIds}
      onExit={() => {
        clearLastSessionId()
        setFlow({ screen: 'home' })
      }}
    />
  )
}

export default App
