import { useState } from 'react'
import { api, ApiError, type Character } from './api/client'
import CharacterCreator from './screens/CharacterCreator'
import PartySetup from './screens/PartySetup'
import CampaignSelect from './screens/CampaignSelect'

type Flow =
  | { screen: 'character' }
  | { screen: 'party'; character: Character }
  | { screen: 'campaign'; character: Character; companionIds: string[] }
  | { screen: 'ready'; sessionId: string }

function App() {
  const [flow, setFlow] = useState<Flow>({ screen: 'character' })
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

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
            setFlow({ screen: 'ready', sessionId: session_id })
          } catch (err) {
            setStartError(err instanceof ApiError ? err.message : 'Failed to reach the server')
          } finally {
            setStarting(false)
          }
        }}
      />
    )
  }

  return (
    <div className="wizard">
      <h1>Session created!</h1>
      <p>
        Session id <code>{flow.sessionId}</code> is recorded and ready. Live play (Day 19) will
        connect to it over the WebSocket from here.
      </p>
    </div>
  )
}

export default App
