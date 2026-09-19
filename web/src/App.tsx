import { useEffect, useState } from 'react'
import { getAccessKey } from './api/accessKey'
import { api, ApiError, type Character } from './api/client'
import AccessGate from './screens/AccessGate'
import CharacterCreator from './screens/CharacterCreator'
import PartySetup from './screens/PartySetup'
import CampaignSelect from './screens/CampaignSelect'
import LivePlay from './screens/LivePlay'

type Flow =
  | { screen: 'character' }
  | { screen: 'party'; character: Character }
  | { screen: 'campaign'; character: Character; companionIds: string[] }
  | { screen: 'live'; sessionId: string; characterId: string }

function App() {
  const [flow, setFlow] = useState<Flow>({ screen: 'character' })
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
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

  if (flow.screen === 'character') {
    return (
      <CharacterCreator onCreated={(character) => setFlow({ screen: 'party', character })} />
    )
  }

  if (flow.screen === 'party') {
    return (
      <PartySetup
        onBack={() => setFlow({ screen: 'character' })}
        onNext={(companionIds) =>
          setFlow({ screen: 'campaign', character: flow.character, companionIds })
        }
      />
    )
  }

  if (flow.screen === 'campaign') {
    const { character, companionIds } = flow
    return (
      <CampaignSelect
        starting={starting}
        startError={startError}
        onBack={() => setFlow({ screen: 'party', character })}
        onStart={async (campaignId) => {
          setStarting(true)
          setStartError(null)
          try {
            const { session_id } = await api.startSession({
              campaign_id: campaignId,
              character_id: character.id,
              companion_ids: companionIds,
            })
            setFlow({ screen: 'live', sessionId: session_id, characterId: character.id })
          } catch (err) {
            setStartError(err instanceof ApiError ? err.message : 'Failed to reach the server')
          } finally {
            setStarting(false)
          }
        }}
      />
    )
  }

  return <LivePlay sessionId={flow.sessionId} myCharacterId={flow.characterId} />
}

export default App
