import type { LiveCharacter } from '../ws/sessionClient'
import InfoTip from './InfoTip'
import { conditionDescription, conditionLabel, exhaustionDescription } from '../utils/conditionDetail'

/** Active effects on a character - conditions (blinded, blessed, etc, each
 * with a real duration) plus exhaustion (tracked separately, see
 * conditionDetail.ts's own docstring for why). Shared by CharacterSheet.tsx
 * (every combatant's compact sidebar card - PCs, companions, and monsters
 * alike) and CharacterDetailSheet.tsx (the player's own full sheet), so the
 * two don't carry two slowly-drifting copies of the same rendering. */
export default function ConditionBadges({ character }: { character: LiveCharacter }) {
  if (character.conditions.length === 0 && character.exhaustion_level === 0) return null

  return (
    <div className="condition-badges">
      {character.conditions.map((condition, i) => {
        const desc = conditionDescription(condition.name, condition.detail)
        return (
          <span className="condition-badge" key={`${condition.name}-${i}`}>
            {conditionLabel(condition)}
            {desc && <InfoTip text={desc} />}
          </span>
        )
      })}
      {character.exhaustion_level > 0 && (
        <span className="condition-badge condition-badge-exhaustion">
          Exhaustion {character.exhaustion_level}
          <InfoTip text={exhaustionDescription(character.exhaustion_level)} />
        </span>
      )}
    </div>
  )
}
