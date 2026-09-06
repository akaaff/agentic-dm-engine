import { useState, type FormEvent } from 'react'
import CharacterSheet from '../components/CharacterSheet'
import CombatGrid from '../components/CombatGrid'
import NarrationFeed from '../components/NarrationFeed'
import { useSessionSocket } from '../ws/sessionClient'

export default function LivePlay({
  sessionId,
  myCharacterId,
}: {
  sessionId: string
  myCharacterId: string
}) {
  const {
    gameState,
    narrationLog,
    sceneImageUrl,
    awaitingActor,
    error,
    connected,
    sendPlayerAction,
    sendPlayerMove,
  } = useSessionSocket(sessionId)
  const [draft, setDraft] = useState('')

  const isMyTurn = awaitingActor === myCharacterId

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!draft.trim()) return
    sendPlayerAction(draft.trim())
    setDraft('')
  }

  if (!connected && !gameState) {
    return <div className="wizard">Connecting to the game server...</div>
  }

  return (
    <div className="live-play">
      <div className="live-play-main">
        <h1>
          {gameState?.encounter_id.replace(/_/g, ' ') ?? 'Adventure'}
          {gameState && ` - round ${gameState.round}`}
        </h1>
        {gameState?.status !== 'in_progress' && gameState && (
          <p className="wizard-error status-banner">
            {gameState.status === 'victory' && 'Victory! The encounter is over.'}
            {gameState.status === 'defeat' && 'Defeat... the party has fallen.'}
            {gameState.status === 'aborted' && 'The encounter ended early.'}
          </p>
        )}
        {gameState?.battle_map && (
          <CombatGrid
            battleMap={gameState.battle_map}
            characters={gameState.characters}
            currentActorId={gameState.turn_order[gameState.current_turn]}
            myCharacterId={myCharacterId}
            canMove={isMyTurn}
            onMoveTo={sendPlayerMove}
          />
        )}
        {sceneImageUrl && (
          // scene_image_url is a local filesystem path (Day 16 - no media
          // endpoint exists yet, see DECISIONS.md), so it can't be rendered
          // as a real <img> src from the browser. Day 21 ("scene image
          // panel") is where a static-files route gets mounted and this
          // becomes an actual picture; for now just surface that one exists.
          <p className="scene-image-placeholder">A new scene image was generated.</p>
        )}
        <NarrationFeed entries={narrationLog} />
        <form className="action-form" onSubmit={handleSubmit}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={isMyTurn ? 'What do you do?' : "Waiting for other turns..."}
            disabled={!isMyTurn}
          />
          <button type="submit" disabled={!isMyTurn || !draft.trim()}>
            Act
          </button>
        </form>
        {error && <p className="wizard-error">{error}</p>}
      </div>
      <div className="live-play-sidebar">
        {gameState &&
          gameState.turn_order.map((id) => {
            const character = gameState.characters[id]
            if (!character) return null
            return (
              <CharacterSheet
                key={id}
                character={character}
                isCurrentTurn={gameState.turn_order[gameState.current_turn] === id}
                isYou={id === myCharacterId}
              />
            )
          })}
      </div>
    </div>
  )
}
