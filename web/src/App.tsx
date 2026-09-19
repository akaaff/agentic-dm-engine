import { useEffect, useState } from 'react'
import { getAccessKey } from './api/accessKey'
import { api, ApiError } from './api/client'
import { getLobbyToken, setLobbyToken } from './api/lobbyTokens'
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
  | { screen: 'lobby'; sessionId: string; characterId: string }
  | { screen: 'live'; sessionId: string; characterId: string }

function App() {
  const [flow, setFlow] = useState<Flow>({ screen: 'home' })
  const [working, setWorking] = useState(false)
  const [workingError, setWorkingError] = useState<string | null>(null)
  // Issue #42: 'checking' avoids a flash of the gate screen (or the real
  // app) before we know whether the backend even requires a passphrase, or
  // whether an already-stored one from a prior visit is still valid.
  const [gate, setGate] = useState<'checking' | 'locked' | 'unlocked'>('checking')

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

  if (gate === 'checking') {
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
            const existingToken = getLobbyToken(code)
            if (existingToken) {
              // Resuming: confirm the stored token is still known to this
              // lobby rather than assuming it - the server may have been
              // reset, or this could be a stale/corrupted local value.
              const { character_id } = await api.joinLobby(code, { token: existingToken })
              setFlow(
                status.status === 'in_progress'
                  ? { screen: 'live', sessionId: code, characterId: character_id }
                  : { screen: 'lobby', sessionId: code, characterId: character_id }
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
        onCreated={async (character) => {
          // The character is already created and persisted at this point -
          // joinLobby's own idempotent-retry handling (issue #44: a repeat
          // join for the same character_id returns the same token rather
          // than erroring) means one automatic retry on a transient failure
          // is safe and simple, without walking back through character
          // creation (which would fail outright - the id already exists).
          for (let attempt = 0; attempt < 2; attempt++) {
            try {
              const { token, character_id } = await api.joinLobby(sessionId, {
                character_id: character.id,
              })
              setLobbyToken(sessionId, token)
              setFlow({ screen: 'lobby', sessionId, characterId: character_id })
              return
            } catch (err) {
              if (attempt === 1) {
                setWorkingError(
                  err instanceof ApiError ? err.message : 'Failed to reach the server'
                )
                setFlow({ screen: 'join_code' })
              }
            }
          }
        }}
      />
    )
  }

  if (flow.screen === 'lobby') {
    const { sessionId, characterId } = flow
    return <Lobby sessionId={sessionId} onStarted={() => setFlow({ screen: 'live', sessionId, characterId })} />
  }

  return <LivePlay sessionId={flow.sessionId} myCharacterId={flow.characterId} />
}

export default App
