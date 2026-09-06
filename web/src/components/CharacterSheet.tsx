import type { LiveCharacter } from '../ws/sessionClient'

export default function CharacterSheet({
  character,
  isCurrentTurn,
  isYou,
}: {
  character: LiveCharacter
  isCurrentTurn: boolean
  isYou: boolean
}) {
  const hpPct = character.max_hp > 0 ? Math.max(0, (character.hp / character.max_hp) * 100) : 0
  const side = character.is_pc ? 'ally' : 'enemy'

  return (
    <div
      className={`character-sheet ${side} ${isCurrentTurn ? 'current-turn' : ''} ${character.is_dead ? 'dead' : ''}`}
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
