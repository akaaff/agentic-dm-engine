import type { LiveCharacter } from '../ws/sessionClient'

export default function CharacterSheet({
  character,
  isCurrentTurn,
  isYou,
  color,
}: {
  character: LiveCharacter
  isCurrentTurn: boolean
  isYou: boolean
  /** This character's actorColors.ts color - a distinct one per PC/
   * companion, red for every enemy. Replaces the old ally/enemy CSS
   * classes (still-blue ally, still-red enemy border, just no longer two
   * hardcoded colors baked into index.css). */
  color: string
}) {
  const hpPct = character.max_hp > 0 ? Math.max(0, (character.hp / character.max_hp) * 100) : 0
  // current-turn's purple highlight (an existing, more urgent "it's your
  // turn" signal) takes priority over the character's own identity color,
  // matching this card's pre-existing visual behavior exactly.
  const borderColor = isCurrentTurn ? '#7c4dff' : color

  return (
    <div
      className={`character-sheet ${isCurrentTurn ? 'current-turn' : ''} ${character.is_dead ? 'dead' : ''}`}
      style={{ borderLeftColor: borderColor, borderLeftWidth: 3, borderLeftStyle: 'solid' }}
    >
      <div className="character-sheet-header">
        <strong>
          {character.name}
          {isYou && ' (you)'}
        </strong>
        {isCurrentTurn && <span className="turn-badge">acting now</span>}
      </div>
      <div className="character-sheet-meta">
        {character.race} {character.class_} - AC {character.ac}
      </div>
      <div className="hp-bar-track">
        <div className="hp-bar-fill" style={{ width: `${hpPct}%` }} />
      </div>
      <div className="hp-label">
        HP {character.hp}/{character.max_hp}
        {character.is_dead && ' - dead'}
        {!character.is_dead && character.hp <= 0 && (character.is_stable ? ' - stable' : ' - unconscious')}
      </div>
      {character.conditions.length > 0 && (
        <div className="conditions">
          {character.conditions.map((c) => c.name).join(', ')}
        </div>
      )}
    </div>
  )
}
