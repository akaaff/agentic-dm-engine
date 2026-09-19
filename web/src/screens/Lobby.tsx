import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from '../api/client'
import PartySetup from './PartySetup'

const POLL_INTERVAL_MS = 2500

// Issue #45: the waiting room between joining a lobby and actually playing.
// No "leader" role exists on the backend (POST /sessions/{id}/start has no
// caller-identity check - see issue #44) - so every player who lands here
// sees the same "start the adventure" option, not just whoever created the
// lobby first. Polls for another player having already started it (no
// lobby-status WebSocket exists, and opening the live-play one early would
// build the actual encounter before companions fill the remaining seats).
export default function Lobby({
  sessionId,
  onStarted,
}: {
  sessionId: string
  onStarted: () => void
}) {
  const [partyCharacterIds, setPartyCharacterIds] = useState<string[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [pickingCompanions, setPickingCompanions] = useState(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const onStartedRef = useRef(onStarted)
  useEffect(() => {
    onStartedRef.current = onStarted
  }, [onStarted])

  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const status = await api.getLobbyStatus(sessionId)
        if (cancelled) return
        setPartyCharacterIds(status.party_character_ids)
        if (status.status === 'in_progress') {
          onStartedRef.current()
          return
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof ApiError ? err.message : 'Failed to reach the server')
        }
      }
      if (!cancelled) {
        timer = window.setTimeout(poll, POLL_INTERVAL_MS)
      }
    }

    let timer = window.setTimeout(poll, 0)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [sessionId])

  async function handleStart(companionIds: string[]) {
    setStarting(true)
    setStartError(null)
    try {
      await api.startLobby(sessionId, companionIds)
      onStarted()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Someone else's Start call (or this browser's own poll) beat this
        // one to it - the game is starting either way, so just proceed.
        onStarted()
        return
      }
      setStartError(err instanceof ApiError ? err.message : 'Failed to reach the server')
    } finally {
      setStarting(false)
    }
  }

  if (pickingCompanions) {
    return (
      <div>
        <PartySetup onBack={() => setPickingCompanions(false)} onNext={handleStart} />
        {starting && <p>Starting the adventure...</p>}
        {startError && <p className="wizard-error">{startError}</p>}
      </div>
    )
  }

  return (
    <div className="wizard">
      <h1>Waiting for the Party</h1>
      <p>
        Share this code with other players so they can join:{' '}
        <strong className="lobby-code">{sessionId}</strong>
      </p>
      <p>
        {partyCharacterIds.length} player{partyCharacterIds.length === 1 ? '' : 's'} joined so
        far.
      </p>
      {loadError && <p className="wizard-error">Could not check lobby status: {loadError}</p>}
      <div className="wizard-nav">
        <button type="button" onClick={() => setPickingCompanions(true)}>
          Start the Adventure
        </button>
      </div>
    </div>
  )
}
