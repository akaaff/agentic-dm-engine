import { useMemo, useState, type FormEvent } from 'react'
import CharacterDetailSheet from '../components/CharacterDetailSheet'
import CharacterSheet from '../components/CharacterSheet'
import CombatGrid from '../components/CombatGrid'
import NarrationFeed from '../components/NarrationFeed'
import SceneImagePanel from '../components/SceneImagePanel'
import { buildActorColorMap } from '../utils/actorColors'
import { computeResourceQuickActions } from '../utils/resourceQuickActions'
import { useSessionSocket, type LiveCharacter } from '../ws/sessionClient'

export default function LivePlay({
  sessionId,
  myCharacterIds,
  onExit,
}: {
  sessionId: string
  /** Every character this player controls - one, or several in multi-character play. */
  myCharacterIds: string[]
  onExit: () => void
}) {
  const {
    gameState,
    combatSummaries,
    narrationLog,
    logCaughtUp,
    sceneImageUrl,
    awaitingActor,
    disconnectedActors,
    bardicOffer,
    shieldOffer,
    protectionOffer,
    partyChoice,
    campaignComplete,
    error,
    connected,
    reconnecting,
    sendPlayerAction,
    sendPlayerMove,
    sendRest,
    sendContinueCampaign,
    sendBardicInspirationResponse,
    sendShieldResponse,
    sendProtectionResponse,
    sendPartyChoiceResponse,
  } = useSessionSocket(sessionId)
  // Everyone seen so far. A summoned creature can leave the fight (its spell ends) and an
  // old log line about it would otherwise show its raw id instead of its name.
  const [knownCharacters, setKnownCharacters] = useState<Record<string, LiveCharacter>>({})
  if (gameState && Object.entries(gameState.characters).some(([id, c]) => knownCharacters[id] !== c)) {
    setKnownCharacters({ ...knownCharacters, ...gameState.characters })
  }
  const [draft, setDraft] = useState('')
  const [choiceDraft, setChoiceDraft] = useState('')
  const mine = useMemo(() => new Set(myCharacterIds), [myCharacterIds])
  // Which of my characters the sheet shows when none of them is up - clicking a
  // tab changes it; while one of them is up, that one is shown (see activeId).
  const [focusedId, setFocusedId] = useState(myCharacterIds[0] ?? '')
  // Issue #38: a per-viewer convenience toggle (localStorage, not shared
  // session state) - shows each attack/skill-check/saving-throw's full
  // modifier breakdown in the combat log, to catch a mechanic gap (a
  // proficiency bonus silently missing or wrongly included, a class feature
  // not applying) by eye during play instead of needing a debug script.
  const [debugMode, setDebugMode] = useState(() => {
    try {
      return localStorage.getItem('dm-debug-mode') === '1'
    } catch {
      return false
    }
  })
  function toggleDebugMode() {
    setDebugMode((prev) => {
      const next = !prev
      try {
        localStorage.setItem('dm-debug-mode', next ? '1' : '0')
      } catch {
        // Private window / blocked storage - the toggle still works for
        // this session, it just won't be remembered next time.
      }
      return next
    })
  }

  // The server can say it's already your turn before the narration log has
  // finished revealing everything that led up to it (staggered on purpose -
  // see sessionClient.ts) - stay disabled until the reader's actually caught
  // up, not just when awaiting_input technically arrives.
  const actingMine = awaitingActor !== null && mine.has(awaitingActor)
  const isMyTurn = actingMine && logCaughtUp
  const catchingUp = actingMine && !logCaughtUp
  // The character the sheet and quick actions are about: whichever of mine is
  // up, otherwise the one picked in the tabs.
  const activeId = actingMine && awaitingActor ? awaitingActor : focusedId
  const actorColors = useMemo(
    () => (gameState ? buildActorColorMap(gameState.turn_order, gameState.characters) : {}),
    [gameState],
  )
  // Issue #27: only meaningful on the player's own actual turn - a resource
  // usable "right now" means usable this turn, not just non-zero on the
  // sheet.
  const me = gameState?.characters[activeId]
  const characters = gameState?.characters
  // Live-reported layout issue: the grid and the scene image used to share
  // a row, capping the grid's width to make room for a placeholder that's
  // usually empty outside of combat. They now occupy the same slot instead
  // - grid while a fight is actually in progress, scene image otherwise
  // (before the first encounter, between encounters, and right after
  // victory/defeat) - so each gets the main column's full width when it's
  // actually the relevant thing to look at, and both line up with the
  // narration log below them.
  const showCombatGrid = Boolean(gameState?.battle_map) && gameState?.status === 'in_progress'
  const resourceQuickActions = useMemo(
    () => (isMyTurn && me && characters ? computeResourceQuickActions(me, characters) : []),
    [isMyTurn, me, characters],
  )

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!draft.trim()) return
    sendPlayerAction(draft.trim())
    setDraft('')
  }

  // With several characters of mine still to answer a party choice, they answer
  // one at a time, in order - this is whose turn to speak it is.
  const choiceSpeaker = partyChoice?.awaiting.find((id) => mine.has(id))

  function handlePartyChoiceSubmit(e: FormEvent) {
    e.preventDefault()
    if (!choiceDraft.trim()) return
    sendPartyChoiceResponse(choiceDraft.trim(), choiceSpeaker)
    setChoiceDraft('')
  }

  if (!connected && !gameState) {
    // A session-setup failure (issue #29) sends an `error` message and
    // closes the socket before any state_update ever arrives - without this
    // check that just looks like a still-connecting spinner forever, with
    // no way to tell a real content/server bug apart from a slow network.
    if (error) {
      return (
        <div className="wizard wizard-error">
          {error}
          <div>
            <button type="button" onClick={onExit}>
              Exit to Main Menu
            </button>
          </div>
        </div>
      )
    }
    return (
      <div className="wizard">
        {reconnecting ? 'Reconnecting to the game server...' : 'Connecting to the game server...'}
      </div>
    )
  }

  return (
    <div className="live-play">
      <div className="live-play-main">
        <div className="live-play-header">
          <h1>
            {gameState?.encounter_id.replace(/_/g, ' ') ?? 'Adventure'}
            {gameState && ` - round ${gameState.round}`}
          </h1>
          <button type="button" className="exit-button" onClick={onExit}>
            Exit to Main Menu
          </button>
        </div>
        <label className="checkbox-row debug-mode-toggle">
          <input type="checkbox" checked={debugMode} onChange={toggleDebugMode} />
          Debug mode (show roll breakdowns)
        </label>
        {gameState?.status !== 'in_progress' && gameState && (
          <p className="wizard-error status-banner">
            {gameState.status === 'victory' && 'Victory! The encounter is over.'}
            {gameState.status === 'defeat' && 'Defeat... the party has fallen.'}
            {gameState.status === 'aborted' && 'The encounter ended early.'}
          </p>
        )}
        {reconnecting && gameState && (
          // The game server dropped this connection (a restart or a network
          // blip) - it keeps every live game in its database, so retrying
          // picks the session back up exactly where it was.
          <p className="wizard-error status-banner">
            Connection to the game server was lost - reconnecting...
          </p>
        )}
        {disconnectedActors.length > 0 && gameState && (
          // Issue #46: surfaces another player's dropped connection so the
          // party isn't left guessing why the game is paused on their turn -
          // this project deliberately waits indefinitely for them rather
          // than auto-skipping (a friends game shouldn't punish a wifi
          // blip), so this is purely informational, no timeout attached.
          <p className="wizard-error status-banner">
            {disconnectedActors
              .map((id) => gameState.characters[id]?.name ?? id)
              .join(', ')}
            {disconnectedActors.length === 1 ? "'s" : "'"} player
            {disconnectedActors.length === 1 ? ' is' : 's are'} reconnecting...
          </p>
        )}
        {gameState?.status === 'victory' && logCaughtUp && !partyChoice && (
          // Issue #28: "victory" is a real stop the party chooses to leave -
          // rest here to recover HP/spell slots/class resources before the
          // campaign's next encounter, or just continue on. Gated on
          // logCaughtUp for the same reason isMyTurn is - don't offer a
          // choice about a state the paced log hasn't actually shown yet.
          // Story-adaptive-encounters Phase 2: also hidden while a
          // party_choice is pending - gameState.status stays "victory" for
          // that pause's whole duration (party_choice never touches it), so
          // without this check these buttons would sit alongside the
          // party-choice panel below, confusingly offering to re-continue a
          // campaign that's already mid-conversation (the server now
          // rejects it too, see _advance_campaign_after_victory/
          // _handle_rest_request's own pending_party_choice guards, but the
          // UI shouldn't offer a dead end in the first place).
          <div className="rest-controls">
            <button type="button" onClick={() => sendRest('short')}>
              Short Rest
            </button>
            <button type="button" onClick={() => sendRest('long')}>
              Long Rest
            </button>
            {!campaignComplete && (
              <button type="button" onClick={sendContinueCampaign}>
                Continue
              </button>
            )}
          </div>
        )}
        {campaignComplete && logCaughtUp && (
          // Story-adaptive-encounters Phase 3: the chain has genuinely run
          // out (including via a model-generated ending, not just an
          // authored one) - Continue is hidden above rather than offering a
          // click that would silently re-walk an already-finished story
          // (see Session.campaign_complete's own docstring). Rest still
          // works - there's nothing wrong with a final rest after the story
          // wraps up.
          <p className="status-banner">The adventure has concluded.</p>
        )}
        <div className="scene-row">
          {showCombatGrid && gameState?.battle_map ? (
            <CombatGrid
              battleMap={gameState.battle_map}
              characters={gameState.characters}
              currentActorId={gameState.turn_order[gameState.current_turn]}
              myCharacterIds={myCharacterIds}
              canMove={isMyTurn}
              onMoveTo={sendPlayerMove}
              actorColors={actorColors}
            />
          ) : (
            <SceneImagePanel url={sceneImageUrl} />
          )}
        </div>
        <NarrationFeed
          entries={narrationLog}
          characters={knownCharacters}
          actorColors={actorColors}
          debugMode={debugMode}
        />
        {bardicOffer && mine.has(bardicOffer.holder) && (
          // Issue #53: the recipient's own choice - shown only to the
          // connection controlling the holder (the server broadcasts the
          // offer to everyone so the rest of the party sees why play is
          // paused, but only this connection can actually answer it - see
          // the informational-only branch just below for everyone else).
          <div className="bardic-inspiration-offer">
            <p>
              Your attack rolled <strong>{bardicOffer.natural}</strong> (total{' '}
              {bardicOffer.total_without_die} vs AC {bardicOffer.defender_ac}) - a miss. Spend your
              Bardic Inspiration (1d{bardicOffer.die_sides}) to try to turn it into a hit?
            </p>
            <div className="bardic-inspiration-offer-buttons">
              <button type="button" onClick={() => sendBardicInspirationResponse(true)}>
                Use it
              </button>
              <button type="button" onClick={() => sendBardicInspirationResponse(false)}>
                Save it
              </button>
            </div>
          </div>
        )}
        {bardicOffer && !mine.has(bardicOffer.holder) && (
          <p className="companion-meta">
            {characters?.[bardicOffer.holder]?.name ?? bardicOffer.holder} is deciding whether to
            spend their Bardic Inspiration...
          </p>
        )}
        {protectionOffer && mine.has(protectionOffer.protector) && (
          // Issue #92: the Protection fighting style - asked before the attack
          // is rolled; same shape as the Shield prompt below.
          <div className="bardic-inspiration-offer">
            <p>
              {characters?.[protectionOffer.attacker]?.name ?? protectionOffer.attacker} is about
              to attack {characters?.[protectionOffer.target]?.name ?? protectionOffer.target}, who
              is next to you. Use your reaction to give the attack disadvantage?
            </p>
            <div className="bardic-inspiration-offer-buttons">
              <button type="button" onClick={() => sendProtectionResponse(true)}>
                Protect them
              </button>
              <button type="button" onClick={() => sendProtectionResponse(false)}>
                Let it come
              </button>
            </div>
          </div>
        )}
        {protectionOffer && !mine.has(protectionOffer.protector) && (
          <p className="companion-meta">
            {characters?.[protectionOffer.protector]?.name ?? protectionOffer.protector} is
            deciding whether to protect{' '}
            {characters?.[protectionOffer.target]?.name ?? protectionOffer.target}...
          </p>
        )}
        {shieldOffer && mine.has(shieldOffer.target) && (
          // Issue #93: the Shield reaction - same shape as the Bardic
          // Inspiration prompt above (everyone sees the pause, only the
          // targeted character's connection can answer).
          <div className="bardic-inspiration-offer">
            <p>
              {characters?.[shieldOffer.attacker]?.name ?? shieldOffer.attacker} hit{' '}
              {characters?.[shieldOffer.target]?.name ?? shieldOffer.target} (total{' '}
              {shieldOffer.attack_total} vs AC {shieldOffer.target_ac}). Cast Shield as a reaction
              (AC {shieldOffer.shield_ac} until the round ends) to turn it into a miss?
            </p>
            <div className="bardic-inspiration-offer-buttons">
              <button type="button" onClick={() => sendShieldResponse(true)}>
                Cast Shield
              </button>
              <button type="button" onClick={() => sendShieldResponse(false)}>
                Take the hit
              </button>
            </div>
          </div>
        )}
        {shieldOffer && !mine.has(shieldOffer.target) && (
          <p className="companion-meta">
            {characters?.[shieldOffer.target]?.name ?? shieldOffer.target} is deciding whether to
            cast Shield...
          </p>
        )}
        {resourceQuickActions.length > 0 && (
          // Issue #27: a prominent, actionable callout - not the passive
          // sidebar stat list - naming exactly what to type. Clicking fills
          // the input rather than submitting outright, since some of these
          // (Flurry of Blows' target, Wild Shape's beast, Bardic
          // Inspiration's ally) are just one reasonable suggestion the
          // player may want to change first.
          <div className="resource-quick-actions">
            {resourceQuickActions.map((qa) => (
              <button key={qa.key} type="button" onClick={() => setDraft(qa.suggestedText)}>
                {qa.label} ({qa.remaining} left)
              </button>
            ))}
          </div>
        )}
        {partyChoice && (
          // Story-adaptive-encounters Phase 2: a narrative decision point,
          // not a turn - hides the ordinary combat action form entirely
          // while this is up, since there's no "turn" happening (the
          // server never sends awaiting_input during a party_choice pause).
          <div className="party-choice-panel">
            <p>The party must decide what to do next.</p>
            {Object.entries(partyChoice.companionResponses).map(([id, text]) => (
              <p key={id} className="companion-meta">
                <strong>{characters?.[id]?.name ?? id}:</strong> {text}
              </p>
            ))}
            {choiceSpeaker ? (
              <form className="action-form" onSubmit={handlePartyChoiceSubmit}>
                <input
                  value={choiceDraft}
                  onChange={(e) => setChoiceDraft(e.target.value)}
                  placeholder={
                    myCharacterIds.length > 1
                      ? `What does ${characters?.[choiceSpeaker]?.name ?? choiceSpeaker} say or do?`
                      : 'What do you say or do?'
                  }
                />
                <button type="submit" disabled={!choiceDraft.trim()}>
                  Respond
                </button>
              </form>
            ) : (
              <p className="companion-meta">
                Waiting on{' '}
                {partyChoice.awaiting.map((id) => characters?.[id]?.name ?? id).join(', ')} to
                respond...
              </p>
            )}
          </div>
        )}
        {!partyChoice && (
          <form className="action-form" onSubmit={handleSubmit}>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={
                isMyTurn
                  ? myCharacterIds.length > 1
                    ? `What does ${me?.name ?? 'your character'} do?`
                    : 'What do you do?'
                  : catchingUp
                    ? 'Catching up...'
                    : 'Waiting for other turns...'
              }
              disabled={!isMyTurn}
            />
            <button type="submit" disabled={!isMyTurn || !draft.trim()}>
              Act
            </button>
          </form>
        )}
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
                isYou={mine.has(id)}
                color={actorColors[id]}
                summonerName={
                  character.summoned_by ? gameState.characters[character.summoned_by]?.name : undefined
                }
              />
            )
          })}
      </div>
      {me && (
        <div className="live-play-detail">
          {myCharacterIds.length > 1 && (
            <div className="my-characters-tabs">
              {myCharacterIds.map((id) => (
                <button
                  key={id}
                  type="button"
                  className={id === activeId ? 'active' : ''}
                  disabled={actingMine}
                  title={actingMine ? 'The sheet follows whichever of your characters is up' : undefined}
                  onClick={() => setFocusedId(id)}
                >
                  {characters?.[id]?.name ?? id}
                  {awaitingActor === id ? ' (up)' : ''}
                </button>
              ))}
            </div>
          )}
          <CharacterDetailSheet character={me} combatSummary={combatSummaries[activeId]} />
        </div>
      )}
    </div>
  )
}
