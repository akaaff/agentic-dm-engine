import { useMemo, useState, type FormEvent } from 'react'
import CharacterDetailSheet from '../components/CharacterDetailSheet'
import CharacterSheet from '../components/CharacterSheet'
import CombatGrid from '../components/CombatGrid'
import NarrationFeed from '../components/NarrationFeed'
import SceneImagePanel from '../components/SceneImagePanel'
import { buildActorColorMap } from '../utils/actorColors'
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
    logCaughtUp,
    sceneImageUrl,
    awaitingActor,
    error,
    connected,
    sendPlayerAction,
    sendPlayerMove,
  } = useSessionSocket(sessionId)
  const [draft, setDraft] = useState('')

  // The server can say it's already your turn before the narration log has
  // finished revealing everything that led up to it (staggered on purpose -
  // see sessionClient.ts) - stay disabled until the reader's actually caught
  // up, not just when awaiting_input technically arrives.
  const isMyTurn = awaitingActor === myCharacterId && logCaughtUp
  const catchingUp = awaitingActor === myCharacterId && !logCaughtUp
  const actorColors = useMemo(
    () => (gameState ? buildActorColorMap(gameState.turn_order, gameState.characters) : {}),
    [gameState],
  )

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!draft.trim()) return
    sendPlayerAction(draft.trim())
    setDraft('')
  }

  if (!connected && !gameState) {
    // A session-setup failure (issue #29) sends an `error` message and
    // closes the socket before any state_update ever arrives - without this
    // check that just looks like a still-connecting spinner forever, with
    // no way to tell a real content/server bug apart from a slow network.
    if (error) {
      return <div className="wizard wizard-error">{error}</div>
    }
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
        <div className="scene-row">
          {gameState?.battle_map && (
            <CombatGrid
              battleMap={gameState.battle_map}
              characters={gameState.characters}
              currentActorId={gameState.turn_order[gameState.current_turn]}
              myCharacterId={myCharacterId}
              canMove={isMyTurn}
              onMoveTo={sendPlayerMove}
              actorColors={actorColors}
            />
          )}
          <SceneImagePanel url={sceneImageUrl} />
        </div>
        <NarrationFeed
          entries={narrationLog}
          characters={gameState?.characters ?? {}}
          actorColors={actorColors}
        />
        <form className="action-form" onSubmit={handleSubmit}>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              isMyTurn ? 'What do you do?' : catchingUp ? 'Catching up...' : 'Waiting for other turns...'
            }
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
                color={actorColors[id]}
              />
            )
          })}
      </div>
      {gameState?.characters[myCharacterId] && (
        <div className="live-play-detail">
          <CharacterDetailSheet character={gameState.characters[myCharacterId]} />
        </div>
      )}
    </div>
  )
}
