import type {
  AbilityScore,
  BackgroundSummary,
  ClassDetail,
  EquipmentSummary,
  RaceSummary,
  StartingEquipmentItem,
} from '../api/client'
import { nameWithEquipmentDetail } from '../utils/equipmentDetail'
import { portraitUrl } from '../utils/portraits'

const ABILITIES: AbilityScore[] = ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA']

function skillLabel(skillIndex: string): string {
  return skillIndex
    .replace(/^skill-/, '')
    .split('-')
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(' ')
}

/** Live-updating preview of the character being built, shown alongside the
 * wizard throughout every step - "built dynamically while character
 * creation" per the original feature request. Reads directly from
 * CharacterCreator's own in-progress state rather than re-fetching
 * anything, so it reflects each choice the instant it's made, including
 * steps not yet reached (shown as placeholders). */
export default function CharacterPreviewSheet({
  name,
  race,
  gender,
  classDetail,
  classIndex,
  className,
  fightingStyle,
  chosenSkills,
  assignments,
  raceBonus,
  finalScore,
  background,
  chosenEquipment,
  equipment,
}: {
  name: string
  race: RaceSummary | undefined
  gender: string
  classDetail: ClassDetail | null
  classIndex: string
  className: string | null
  fightingStyle: string
  chosenSkills: string[]
  assignments: Record<AbilityScore, number | ''>
  raceBonus: (ability: AbilityScore) => number
  finalScore: (ability: AbilityScore) => number | null
  background: BackgroundSummary | undefined
  chosenEquipment: string[]
  equipment: EquipmentSummary[]
}) {
  const portrait = portraitUrl({
    race_index: race?.index ?? null,
    class_index: classIndex || null,
    gender: gender || null,
  })
  const equipmentNames = new Map(equipment.map((e) => [e.index, e.name]))
  const equipmentByIndex = new Map(equipment.map((e) => [e.index, e]))
  // Issue #32: the class/background's *fixed* starting kit, combined the
  // same way character_creation.create_character's inventory-building loop
  // combines them (class items, then background items) - distinct from
  // chosenEquipment, which is only the optional proficiency-gated picks.
  const startingKit: StartingEquipmentItem[] = [
    ...(classDetail?.starting_equipment ?? []),
    ...(background?.starting_equipment ?? []),
  ]
  const startingKitLabel = (item: StartingEquipmentItem): string => {
    const full = equipmentByIndex.get(item.index)
    const name = full ? nameWithEquipmentDetail(full) : item.name
    return item.quantity > 1 ? `${name} x${item.quantity}` : name
  }

  return (
    <aside className="character-preview-sheet sheet">
      <div className="preview-portrait">
        {portrait ? (
          <img
            src={portrait}
            alt={`${name || 'Character'} portrait`}
            onError={(e) => {
              e.currentTarget.style.display = 'none'
            }}
          />
        ) : (
          <div className="preview-portrait-placeholder">
            {race && classIndex && !gender
              ? 'Choose a gender for a portrait'
              : 'Portrait appears once race, class & gender are chosen'}
          </div>
        )}
      </div>

      <h2>{name || 'Unnamed hero'}</h2>
      <p className="companion-meta">
        {race?.name ?? 'No race chosen'} {className ?? ''}
        {background ? ` - ${background.name}` : ''}
      </p>

      <table>
        <tbody>
          {ABILITIES.map((a) => {
            const base = assignments[a]
            const bonus = raceBonus(a)
            return (
              <tr key={a}>
                <td>{a}</td>
                <td>{base === '' ? '-' : base}</td>
                <td>
                  {bonus > 0 && finalScore(a) !== null && (
                    <span className="race-bonus-badge">-&gt; {finalScore(a)}</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {fightingStyle && (
        <p>
          <strong>Fighting Style:</strong> {fightingStyle[0].toUpperCase() + fightingStyle.slice(1)}
        </p>
      )}

      <p>
        <strong>Skills:</strong>{' '}
        {chosenSkills.length > 0 ? chosenSkills.map(skillLabel).join(', ') : 'none chosen yet'}
      </p>

      {classDetail && classDetail.cantrips.length > 0 && (
        <p>
          <strong>Cantrips available:</strong>{' '}
          {classDetail.cantrips.map((c) => c.name).join(', ')}
        </p>
      )}

      {startingKit.length > 0 && (
        <p>
          <strong>Starting kit:</strong> {startingKit.map(startingKitLabel).join(', ')}
        </p>
      )}

      <p>
        <strong>Extra gear:</strong>{' '}
        {chosenEquipment.length > 0
          ? chosenEquipment.map((i) => equipmentNames.get(i) ?? i).join(', ')
          : 'none chosen yet'}
      </p>
    </aside>
  )
}
